#!/usr/bin/env python3
"""Render the Cloud Alertmanager config, run it in a local Alertmanager and assert on the
Telegram messages it would send. Requires Docker with host networking."""

import argparse
import json
import os
import re
import shutil
import socket
import socketserver
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
CRONJOB = AM_DIR.parent / "cronjob-alertmanager-config-sync.yaml"

YQ_IMAGE = "mikefarah/yq:4.54.1"
AM_IMAGE = "prom/alertmanager:v0.34.1"
CAPTURE_PORT = 18099
SMTP_PORT = 2525
AM_PORT = 19093
AM_URL = f"http://127.0.0.1:{AM_PORT}"
TELEGRAM_LIMIT = 4096
FALLBACK = "Alertmanager notification could not be sent"

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
    "email_configs[].smarthost": f"127.0.0.1:{SMTP_PORT}",
    "email_configs[].force_implicit_tls": "false (plain SMTP to the local stub)",
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


def render_expr() -> str:
    """The yq expression the CronJob's render initContainer runs, so tests exercise it."""
    cmd = yq(".spec.jobTemplate.spec.template.spec.initContainers[0].args[0]",
             f"/in/{CRONJOB.name}", mounts={CRONJOB.parent: "/in"})
    match = re.fullmatch(r"yq '(.+)' /config/alertmanager-config\.yaml > /work/rendered\.yaml\s*",
                         cmd, re.DOTALL)
    if not match:
        raise RuntimeError(f"unexpected render command in {CRONJOB.name}: {cmd!r}")
    return match.group(1)


def render(config: Path, env: dict[str, str]) -> str:
    return yq(render_expr(), f"/in/{config.name}", env=env, mounts={config.parent: "/in"})


def apply_test_overrides(rendered: str, tmpl: Path | None) -> str:
    expr = (
        '(.receivers[] | select(has("telegram_configs")) | .telegram_configs[].api_url)'
        f' = "{OVERRIDES["telegram_configs[].api_url"]}"'
        ' | (.receivers[] | select(has("email_configs")) | .email_configs[].smarthost)'
        f' = "{OVERRIDES["email_configs[].smarthost"]}"'
        ' | (.receivers[] | select(has("email_configs")) | .email_configs[].force_implicit_tls)'
        ' = false'
        f' | .route.group_wait = "{OVERRIDES["route.group_wait"]}"'
        f' | .route.group_interval = "{OVERRIDES["route.group_interval"]}"'
        f' | .route.repeat_interval = "{OVERRIDES["route.repeat_interval"]}"'
    )
    if tmpl is not None:
        expr += ' | .templates = ["/etc/alertmanager/telegram.tmpl"]'
    return yq(expr, "-", stdin=rendered)


def assert_port_free(port: int) -> None:
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"port {port} is busy; a stale run? "
                               "docker ps --filter name=am-test-") from None


class CaptureServer:
    def __init__(self, port: int):
        self.messages: list[str] = []
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                if "json" in self.headers.get("Content-Type", ""):
                    payload = {k: str(v) for k, v in json.loads(body).items()}
                else:
                    payload = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
                if self.path.endswith("/sendMessage"):
                    with capture._lock:
                        capture.messages.append(payload.get("text", ""))
                        capture.calls.append((self.path, payload.get("chat_id", "")))
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
            self.calls.clear()

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.messages)


@dataclass
class Envelope:
    mail_from: str = ""
    rcpt_to: list[str] = field(default_factory=list)
    data: str = ""


class SmtpStub:
    """Plaintext SMTP stub recording what Alertmanager sends."""

    def __init__(self, port: int):
        self.envelopes: list[Envelope] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, line: str) -> None:
                self.wfile.write(f"{line}\r\n".encode())

            def handle(self):
                env = Envelope()
                self.reply("220 stub ESMTP")
                for raw in self.rfile:
                    cmd = raw.decode(errors="replace").strip()
                    verb = cmd.split(" ", 1)[0].upper()
                    if verb in ("EHLO", "HELO"):
                        self.reply("250-stub")
                        self.reply("250 8BITMIME")
                    elif verb == "MAIL":
                        env.mail_from = cmd.partition("<")[2].partition(">")[0]
                        self.reply("250 OK")
                    elif verb == "RCPT":
                        env.rcpt_to.append(cmd.partition("<")[2].partition(">")[0])
                        self.reply("250 OK")
                    elif verb == "DATA":
                        self.reply("354 end with .")
                        lines = []
                        for data_line in self.rfile:
                            if data_line in (b".\r\n", b".\n"):
                                break
                            lines.append(data_line.decode(errors="replace"))
                        env.data = "".join(lines)
                        with stub._lock:
                            stub.envelopes.append(env)
                        env = Envelope()
                        self.reply("250 OK")
                    elif verb == "QUIT":
                        self.reply("221 bye")
                        return
                    else:
                        self.reply("250 OK")

        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def reset(self) -> None:
        with self._lock:
            self.envelopes.clear()

    def snapshot(self) -> list[Envelope]:
        with self._lock:
            return list(self.envelopes)


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
    def __init__(self, capture: CaptureServer, smtp: SmtpStub, config_text: str):
        self.capture = capture
        self.smtp = smtp
        self.config_text = config_text

    @contextmanager
    def alertmanager(self):
        workdir = Path(tempfile.mkdtemp(prefix="am-test-"))
        name = f"am-test-{os.getpid()}-{time.monotonic_ns()}"
        assert_port_free(AM_PORT)
        try:
            (workdir / "alertmanager.yml").write_text(self.config_text)
            if TEMPLATE.exists():
                shutil.copy(TEMPLATE, workdir / "telegram.tmpl")
            subprocess.run([
                "docker", "run", "-d", "--rm", "--name", name, "--network", "host",
                "-u", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{workdir}:/etc/alertmanager:ro", AM_IMAGE,
                "--config.file=/etc/alertmanager/alertmanager.yml",
                f"--web.listen-address=127.0.0.1:{AM_PORT}", "--cluster.listen-address=",
                "--storage.path=/tmp",
            ], check=True, capture_output=True)
            self._wait_ready()
            self.capture.reset()
            self.smtp.reset()
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


def node_not_ready(cluster: str = "wieseschwarm") -> AlertSpec:
    return AlertSpec(labels={"alertname": "NodeNotReady", "node": "wieseschwarm-2",
                             "condition": "Ready", "status": "true", "severity": "critical",
                             **KSM_LABELS, "cluster": cluster},
                     annotations={"summary": "Node wieseschwarm-2 is NotReady"})


def headers(msgs: list[str]) -> list[str]:
    return sorted(m.splitlines()[0] for m in msgs)


def scenario_inhibition(h: Harness) -> None:
    targets = daemonset_alerts()[:2] + [
        AlertSpec(labels={"alertname": "PodStuck", "namespace": "monitoring", "phase": "Pending",
                          "pod": "grafana-alloy-node-exporter-j5dj2", "severity": "critical",
                          **KSM_LABELS}),
    ]
    unrelated = AlertSpec(labels={"alertname": "KubeJobFailed", "namespace": "k8up",
                                  "job_name": "backup-x", "severity": "warning", **KSM_LABELS})
    with h.alertmanager():
        h.post_alerts([node_not_ready(), *targets, unrelated])
        msgs = h.wait_messages(count=3, timeout=10)
        live = h.get_alerts()
    assert headers(msgs) == ["KubeJobFailed [warning]", "NodeNotReady [critical]"], headers(msgs)
    muted = [a for a in live if a["labels"]["alertname"] in ("KubeDaemonSetRolloutStuck", "PodStuck")]
    assert len(muted) == 3, len(muted)
    assert all(a["status"]["inhibitedBy"] for a in muted), [a["status"] for a in muted]


def scenario_inhibition_cluster_scoped(h: Harness) -> None:
    other = daemonset_alerts()[0]
    other.labels["cluster"] = "other"
    with h.alertmanager():
        h.post_alerts([node_not_ready(), other])
        msgs = h.wait_messages(count=2, timeout=10)
    assert "KubeDaemonSetRolloutStuck [warning]" in headers(msgs), headers(msgs)


def scenario_render_missing_variable(h: Harness) -> None:
    env = {k: v for k, v in DUMMY_ENV.items() if k != "SMTP_PASSWORD"}
    try:
        render(CONFIG, env)
    except subprocess.CalledProcessError as exc:
        assert "SMTP_PASSWORD" in exc.stderr, exc.stderr
    else:
        raise AssertionError("render succeeded although SMTP_PASSWORD was unset")


def scenario_render_hostile_value(h: Harness) -> None:
    hostile = '*x: #y "q" ${Z} {{ .X }}'
    rendered = render(CONFIG, {**DUMMY_ENV, "SMTP_PASSWORD": hostile, "TELEGRAM_CHAT_ID": "-1001"})
    password = yq('.receivers[] | select(.name == "email") | .email_configs[0].auth_password',
                  "-", stdin=rendered).rstrip("\n")
    assert password == hostile, repr(password)
    chat = yq('.receivers[] | select(.name == "telegram") | .telegram_configs[0].chat_id'
              ' | [tag, .] | join(" ")', "-", stdin=rendered).strip()
    assert chat == "!!int -1001", chat


def scenario_render_empty_variable(h: Harness) -> None:
    try:
        render(CONFIG, {**DUMMY_ENV, "SMTP_PASSWORD": ""})
    except subprocess.CalledProcessError as exc:
        assert "SMTP_PASSWORD" in exc.stderr, exc.stderr
    else:
        raise AssertionError("render succeeded although SMTP_PASSWORD was empty")


def scenario_placeholders_match_cronjob(h: Harness) -> None:
    placeholders = set(re.findall(r"\$\{([A-Z_]+)\}", CONFIG.read_text()))
    cron_env = set(yq(".spec.jobTemplate.spec.template.spec.initContainers[0].env[].name",
                      f"/in/{CRONJOB.name}", mounts={CRONJOB.parent: "/in"}).split())
    assert placeholders == cron_env, f"config {sorted(placeholders)} != CronJob {sorted(cron_env)}"
    assert placeholders == set(DUMMY_ENV), f"config {sorted(placeholders)} != DUMMY_ENV"


def scenario_render_field_mapping(h: Harness) -> None:
    rendered = render(CONFIG, DUMMY_ENV)
    fields = json.loads(yq(
        '{"bot_token": (.receivers[] | select(.name == "telegram") | .telegram_configs[0].bot_token),'
        ' "chat_id": (.receivers[] | select(.name == "telegram") | .telegram_configs[0].chat_id),'
        ' "to": (.receivers[] | select(.name == "email") | .email_configs[0].to),'
        ' "from": (.receivers[] | select(.name == "email") | .email_configs[0].from),'
        ' "smarthost": (.receivers[] | select(.name == "email") | .email_configs[0].smarthost),'
        ' "auth_username": (.receivers[] | select(.name == "email") | .email_configs[0].auth_username),'
        ' "auth_password": (.receivers[] | select(.name == "email") | .email_configs[0].auth_password)}',
        "-o=json", "-", stdin=rendered))
    assert fields == {
        "bot_token": DUMMY_ENV["TELEGRAM_BOT_TOKEN"], "chat_id": int(DUMMY_ENV["TELEGRAM_CHAT_ID"]),
        "to": DUMMY_ENV["SMTP_TO"], "from": DUMMY_ENV["SMTP_FROM"],
        "smarthost": DUMMY_ENV["SMTP_SMARTHOST"], "auth_username": DUMMY_ENV["SMTP_USERNAME"],
        "auth_password": DUMMY_ENV["SMTP_PASSWORD"],
    }, fields


def scenario_telegram_request(h: Harness) -> None:
    with h.alertmanager():
        h.post_alerts([AlertSpec(labels={"alertname": "RequestTest", "severity": "warning"})])
        h.wait_messages(count=1)
        calls = list(h.capture.calls)
    expected = (f"/bot{DUMMY_ENV['TELEGRAM_BOT_TOKEN']}/sendMessage", DUMMY_ENV["TELEGRAM_CHAT_ID"])
    assert calls == [expected], calls


def scenario_cap_boundary(h: Harness) -> None:
    alerts = [AlertSpec(labels={"alertname": name, "instance": f"i{i}", "severity": "warning"})
              for name, count in (("CapTen", 10), ("CapEleven", 11)) for i in range(count)]
    with h.alertmanager():
        h.post_alerts(alerts)
        msgs = {m.splitlines()[0]: m for m in h.wait_messages(count=2)}
    ten, eleven = msgs["CapTen [warning]"], msgs["CapEleven [warning]"]
    assert len(bullets(ten)) == 10 and "… and" not in ten, ten
    assert len(bullets(eleven)) == 10 and "… and 1 more" in eleven, eleven


def scenario_truncation_exact(h: Harness) -> None:
    alerts = [
        AlertSpec(labels={"alertname": "TruncTest", "instance": "long"},
                  annotations={"description": "x" * 300}),
        AlertSpec(labels={"alertname": "TruncTest", "instance": "exact"},
                  annotations={"description": "y" * 180}),
    ]
    with h.alertmanager():
        h.post_alerts(alerts)
        msgs = h.wait_messages(count=1)
    lines = bullets(msgs[0])
    assert "• " + "x" * 180 + "…" in lines, [len(line) for line in lines]
    assert "• " + "y" * 180 in lines, [len(line) for line in lines]


def scenario_resolved_only(h: Harness) -> None:
    alerts = [AlertSpec(labels={"alertname": "ResolvedOnlyTest", "instance": f"i{i}",
                                "severity": "warning"}) for i in range(2)]
    with h.alertmanager():
        h.post_alerts(alerts)
        assert len(h.wait_messages(count=1)) == 1
        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        for alert in alerts:
            alert.ends_at = past
        h.post_alerts(alerts)
        msgs = h.wait_messages(count=2, timeout=20)
    assert len(msgs) == 2, f"expected 2 messages, got {len(msgs)}"
    assert "✅ Resolved: 2" in msgs[1] and "Firing" not in msgs[1], msgs[1]


def scenario_mixed_severity_header(h: Harness) -> None:
    alerts = [AlertSpec(labels={"alertname": "MixSevTest", "instance": sev, "severity": sev})
              for sev in ("warning", "info")]
    with h.alertmanager():
        h.post_alerts(alerts)
        msgs = h.wait_messages(count=1)
    assert len(msgs) == 1, f"expected 1 message, got {len(msgs)}"
    assert msgs[0].splitlines()[0] == "MixSevTest", msgs[0].splitlines()[0]


def scenario_routing_by_severity(h: Harness) -> None:
    alerts = [AlertSpec(labels={"alertname": "RouteCrit", "severity": "critical"}),
              AlertSpec(labels={"alertname": "RouteWarn", "severity": "warning"})]
    with h.alertmanager():
        h.post_alerts(alerts)
        msgs = h.wait_messages(count=2)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not h.smtp.snapshot():
            time.sleep(0.2)
        time.sleep(2)
        mails = h.smtp.snapshot()
    assert headers(msgs) == ["RouteCrit [critical]", "RouteWarn [warning]"], headers(msgs)
    assert len(mails) == 1, f"expected 1 mail (critical only), got {len(mails)}"
    assert mails[0].rcpt_to == [DUMMY_ENV["SMTP_TO"]], mails[0].rcpt_to
    assert mails[0].mail_from == DUMMY_ENV["SMTP_FROM"], mails[0].mail_from
    assert "RouteCrit" in mails[0].data and "RouteWarn" not in mails[0].data


SCENARIOS = {
    "daemonset_flood": scenario_daemonset_flood,
    "cap_overflow": scenario_cap_overflow,
    "mixed_firing_resolved": scenario_mixed_firing_resolved,
    "special_characters": scenario_special_characters,
    "annotation_fallbacks": scenario_annotation_fallbacks,
    "inhibition": scenario_inhibition,
    "inhibition_cluster_scoped": scenario_inhibition_cluster_scoped,
    "render_missing_variable": scenario_render_missing_variable,
    "render_hostile_value": scenario_render_hostile_value,
    "render_empty_variable": scenario_render_empty_variable,
    "placeholders_match_cronjob": scenario_placeholders_match_cronjob,
    "render_field_mapping": scenario_render_field_mapping,
    "telegram_request": scenario_telegram_request,
    "cap_boundary": scenario_cap_boundary,
    "truncation_exact": scenario_truncation_exact,
    "resolved_only": scenario_resolved_only,
    "mixed_severity_header": scenario_mixed_severity_header,
    "routing_by_severity": scenario_routing_by_severity,
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

    harness = Harness(CaptureServer(CAPTURE_PORT), SmtpStub(SMTP_PORT), config_text)
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
