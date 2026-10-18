#!/usr/bin/env python3
"""Render the Cloud Alertmanager config, run it in a local Alertmanager and assert on the
Telegram messages it would send. Requires Docker with host networking."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
AM_DIR = REPO / "kubernetes/01_infrastructure/grafana-alloy/config/alertmanager"
CONFIG = AM_DIR / "alertmanager-config.yaml"
TEMPLATE = AM_DIR / "telegram.tmpl"
RULES_DIR = REPO / "kubernetes/01_infrastructure/grafana-alloy/cluster-rules"

YQ_IMAGE = "mikefarah/yq:4.54.1"
AM_IMAGE = "prom/alertmanager:v0.34.1"
CAPTURE_PORT = 18099
AM_URL = "http://127.0.0.1:19093"
TELEGRAM_LIMIT = 4096
FALLBACK = "Alertmanager notification could not be sent"

RENDER_EXPR = (
    '(.. | select(tag == "!!str")) |= envsubst(nu,ne)'
    ' | (.receivers[] | select(has("telegram_configs")) | .telegram_configs[].chat_id)'
    ' tag = "!!int"'
)
DUMMY_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456:test-token",
    "TELEGRAM_CHAT_ID": "42",
    "SMTP_TO": "to@example.invalid",
    "SMTP_FROM": "from@example.invalid",
    "SMTP_USERNAME": "user@example.invalid",
    "SMTP_PASSWORD": "dummy",
    "SMTP_SMARTHOST": "smtp.example.invalid:465",
}
OVERRIDES = {
    "telegram_configs[].api_url": f"http://127.0.0.1:{CAPTURE_PORT}",
    "email_configs[].smarthost": "127.0.0.1:2525",
    "route.group_wait": "1s",
    "route.group_interval": "5s",
    "route.repeat_interval": "1h",
    "templates": "[/etc/alertmanager/telegram.tmpl] (only if telegram.tmpl exists)",
}

DAEMONSETS = [
    ("monitoring", "grafana-alloy-node-exporter"),
    ("piraeus-datastore", "ha-controller"),
    ("kube-system", "kube-flannel"),
    ("kube-system", "kube-proxy"),
    ("piraeus-datastore", "linstor-csi-nfs-server"),
    ("piraeus-datastore", "linstor-csi-node"),
    ("piraeus-datastore", "linstor-satellite.wieseschwarm-2"),
    ("metallb-system", "metallb-frr-k8s"),
    ("metallb-system", "metallb-speaker"),
]
KSM_LABELS = {
    "cluster": "wieseschwarm",
    "instance": "10.244.0.173:8080",
    "job": "integrations/kubernetes/kube-state-metrics",
    "k8s_cluster_name": "wieseschwarm",
    "source": "kubernetes",
}


def yq(expr: str, *args: str, stdin: str | None = None, env: dict[str, str] | None = None,
       mounts: dict[Path, str] | None = None) -> str:
    cmd = ["docker", "run", "--rm", "-i", "-u", f"{os.getuid()}:{os.getgid()}"]
    for host, container in (mounts or {}).items():
        cmd += ["-v", f"{host}:{container}:ro"]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd += [YQ_IMAGE, expr, *args]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=True).stdout


def render(config: Path, env: dict[str, str]) -> str:
    return yq(RENDER_EXPR, f"/in/{config.name}", env=env, mounts={config.parent: "/in"})


def apply_test_overrides(rendered: str, tmpl: Path | None) -> str:
    expr = (
        '(.receivers[] | select(has("telegram_configs")) | .telegram_configs[].api_url)'
        f' = "{OVERRIDES["telegram_configs[].api_url"]}"'
        ' | (.receivers[] | select(has("email_configs")) | .email_configs[].smarthost)'
        f' = "{OVERRIDES["email_configs[].smarthost"]}"'
        f' | .route.group_wait = "{OVERRIDES["route.group_wait"]}"'
        f' | .route.group_interval = "{OVERRIDES["route.group_interval"]}"'
        f' | .route.repeat_interval = "{OVERRIDES["route.repeat_interval"]}"'
    )
    if tmpl is not None:
        expr += ' | .templates = ["/etc/alertmanager/telegram.tmpl"]'
    return yq(expr, "-", stdin=rendered)




class CaptureServer:
    def __init__(self, port: int):
        self.messages: list[str] = []
        self._lock = threading.Lock()
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                if "json" in self.headers.get("Content-Type", ""):
                    text = json.loads(body).get("text", "")
                else:
                    text = urllib.parse.parse_qs(body).get("text", [""])[0]
                if self.path.endswith("/sendMessage"):
                    with capture._lock:
                        capture.messages.append(text)
                reply = json.dumps({"ok": True, "result": {
                    "message_id": 1, "date": 0, "chat": {"id": 42, "type": "private"}}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def reset(self) -> None:
        with self._lock:
            self.messages.clear()

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.messages)


@dataclass
class AlertSpec:
    labels: dict[str, str]
    annotations: dict[str, str] = field(default_factory=dict)
    generator_url: str = ""
    ends_at: datetime | None = None

    def to_api(self) -> dict:
        alert = {"labels": self.labels, "annotations": self.annotations}
        if self.generator_url:
            alert["generatorURL"] = self.generator_url
        if self.ends_at:
            alert["endsAt"] = self.ends_at.isoformat()
        return alert


class Harness:
    def __init__(self, capture: CaptureServer, config_text: str):
        self.capture = capture
        self.config_text = config_text

    @contextmanager
    def alertmanager(self):
        workdir = Path(tempfile.mkdtemp(prefix="am-test-"))
        name = f"am-test-{os.getpid()}-{time.monotonic_ns()}"
        try:
            (workdir / "alertmanager.yml").write_text(self.config_text)
            if TEMPLATE.exists():
                shutil.copy(TEMPLATE, workdir / "telegram.tmpl")
            subprocess.run([
                "docker", "run", "-d", "--rm", "--name", name, "--network", "host",
                "-u", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{workdir}:/etc/alertmanager:ro", AM_IMAGE,
                "--config.file=/etc/alertmanager/alertmanager.yml",
                "--web.listen-address=127.0.0.1:19093", "--cluster.listen-address=",
                "--storage.path=/tmp",
            ], check=True, capture_output=True)
            self._wait_ready()
            self.capture.reset()
            yield self
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            shutil.rmtree(workdir, ignore_errors=True)

    def _wait_ready(self, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{AM_URL}/-/ready", timeout=1):
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("Alertmanager did not become ready")

    def post_alerts(self, alerts: list[AlertSpec]) -> None:
        data = json.dumps([a.to_api() for a in alerts]).encode()
        req = urllib.request.Request(f"{AM_URL}/api/v2/alerts", data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            pass

    def get_alerts(self) -> list[dict]:
        with urllib.request.urlopen(f"{AM_URL}/api/v2/alerts", timeout=5) as resp:
            return json.load(resp)

    def wait_messages(self, count: int, timeout: float = 15, settle: float = 2) -> list[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(self.capture.snapshot()) < count:
            time.sleep(0.2)
        time.sleep(settle)
        return self.capture.snapshot()


def rule_expr(alertname: str) -> str:
    out = yq(f'.spec.groups[].rules[] | select(.alert == "{alertname}") | .expr',
             *[f"/rules/{p.name}" for p in sorted(RULES_DIR.glob("*.yaml"))],
             mounts={RULES_DIR: "/rules"})
    if not out.strip():
        raise RuntimeError(f"rule {alertname} not found")
    return out


def daemonset_alerts() -> list[AlertSpec]:
    url = ("https://grafana.example.invalid/graph?g0.expr="
           + urllib.parse.quote_plus(rule_expr("KubeDaemonSetRolloutStuck")) + "&g0.tab=1")
    return [
        AlertSpec(
            labels={"alertname": "KubeDaemonSetRolloutStuck", "daemonset": ds,
                    "namespace": ns, "severity": "warning", **KSM_LABELS},
            annotations={
                "summary": "DaemonSet rollout is stuck.",
                "description": f"DaemonSet {ns}/{ds} has not finished or progressed"
                               " for at least 15 minutes.",
            },
            generator_url=url,
        )
        for ns, ds in DAEMONSETS
    ]


def scenario_daemonset_flood(h: Harness) -> None:
    alerts = daemonset_alerts()
    assert len(alerts[0].generator_url) > 1300, len(alerts[0].generator_url)
    with h.alertmanager():
        h.post_alerts(alerts)
        msgs = h.wait_messages(count=1, timeout=15)
    assert len(msgs) == 1, f"expected 1 message, got {len(msgs)}"
    text = msgs[0]
    assert_sendable(text)
    assert text.splitlines()[0] == "KubeDaemonSetRolloutStuck [warning]", text.splitlines()[0]
    assert "Firing: 9" in text
    assert all(f"{ns}/{ds}" in text for ns, ds in DAEMONSETS)
    assert "… and" not in text


LONG_DESCRIPTION = ("'&<>\"x" * 200)[:1000]


def bullets(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("• ")]


def assert_sendable(text: str) -> None:
    assert FALLBACK not in text, f"fallback sent: {text.splitlines()[0]!r}"
    assert len(text) < TELEGRAM_LIMIT, f"{len(text)} runes, Alertmanager truncates at {TELEGRAM_LIMIT}"


def scenario_cap_overflow(h: Harness) -> None:
    alerts = [AlertSpec(labels={"alertname": "CapTest", "instance": f"i{i}", "severity": "warning"},
                        annotations={"description": LONG_DESCRIPTION}) for i in range(30)]
    with h.alertmanager():
        h.post_alerts(alerts)
        msgs = h.wait_messages(count=1)
    assert len(msgs) == 1, f"expected 1 message, got {len(msgs)}"
    text = msgs[0]
    assert_sendable(text)
    lines = bullets(text)
    assert len(lines) == 10, len(lines)
    assert "… and 20 more" in text
    assert max(len(line) for line in lines) <= 183, [len(line) for line in lines]


def scenario_mixed_firing_resolved(h: Harness) -> None:
    alerts = [AlertSpec(labels={"alertname": "MixTest", "instance": f"i{i}", "severity": "warning"},
                        annotations={"description": LONG_DESCRIPTION}) for i in range(30)]
    with h.alertmanager():
        h.post_alerts(alerts)
        assert len(h.wait_messages(count=1)) == 1
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        for alert in alerts[:15]:
            alert.ends_at = past
        h.post_alerts(alerts[:15])
        msgs = h.wait_messages(count=2, timeout=20)
    assert len(msgs) == 2, f"expected 2 messages, got {len(msgs)}"
    text = msgs[1]
    assert_sendable(text)
    assert "Firing: 15" in text and "Resolved: 15" in text
    assert "🔥" in text and "✅" in text
    assert len(bullets(text)) == 20, len(bullets(text))
    assert text.count("… and 5 more") == 2


def scenario_special_characters(h: Harness) -> None:
    alert = AlertSpec(labels={"alertname": "SpecialCharTest", "severity": "warning"},
                      annotations={"description": "<script>alert(1)</script> & co"})
    with h.alertmanager():
        h.post_alerts([alert])
        msgs = h.wait_messages(count=1)
    assert len(msgs) == 1, f"expected 1 message, got {len(msgs)}"
    assert "• <script>alert(1)</script> & co" in bullets(msgs[0]), msgs[0]


def scenario_annotation_fallbacks(h: Harness) -> None:
    alerts = [
        AlertSpec(labels={"alertname": "FallbackTest", "instance": "summary"},
                  annotations={"summary": "only summary"}),
        AlertSpec(labels={"alertname": "FallbackTest", "instance": "bare"}),
    ]
    with h.alertmanager():
        h.post_alerts(alerts)
        msgs = h.wait_messages(count=1)
    assert len(msgs) == 1, f"expected 1 message, got {len(msgs)}"
    lines = bullets(msgs[0])
    assert "• only summary" in lines, msgs[0]
    assert "• FallbackTest" in lines, msgs[0]


SCENARIOS = {
    "daemonset_flood": scenario_daemonset_flood,
    "cap_overflow": scenario_cap_overflow,
    "mixed_firing_resolved": scenario_mixed_firing_resolved,
    "special_characters": scenario_special_characters,
    "annotation_fallbacks": scenario_annotation_fallbacks,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=sorted(SCENARIOS), metavar="SCENARIO")
    args = parser.parse_args()

    print("Test-only differences from the production config:")
    for key, value in OVERRIDES.items():
        print(f"  {key} = {value}")

    tmpl = TEMPLATE if TEMPLATE.exists() else None
    config_text = apply_test_overrides(render(CONFIG, DUMMY_ENV), tmpl)
    with tempfile.TemporaryDirectory(prefix="am-check-") as d:
        Path(d, "alertmanager.yml").write_text(config_text)
        if tmpl:
            shutil.copy(tmpl, Path(d, "telegram.tmpl"))
        check = subprocess.run(
            ["docker", "run", "--rm", "-u", f"{os.getuid()}:{os.getgid()}", "--entrypoint", "amtool",
             "-v", f"{d}:/etc/alertmanager:ro",
             AM_IMAGE, "check-config", "/etc/alertmanager/alertmanager.yml"],
            capture_output=True, text=True)
    if check.returncode != 0:
        print(check.stdout + check.stderr)
        print("amtool check-config failed; aborting")
        return 2

    harness = Harness(CaptureServer(CAPTURE_PORT), config_text)
    selected = args.only or list(SCENARIOS)
    failed = []
    for name in selected:
        try:
            SCENARIOS[name](harness)
            print(f"PASS {name}")
        except Exception as exc:
            failed.append(name)
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            if not isinstance(exc, AssertionError):
                traceback.print_exc()
    print(f"{len(selected) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
