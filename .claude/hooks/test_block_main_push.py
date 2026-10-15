import json
import os
import subprocess
import sys
import tempfile
import unittest

from block_main_push import check

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "block_main_push.py")


def init_repo(path, branch):
    subprocess.run(["git", "init", "-q", "-b", branch, path], check=True)
    return path


class RepoTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.on_main = init_repo(os.path.join(cls._tmp.name, "on-main"), "main")
        cls.on_feature = init_repo(os.path.join(cls._tmp.name, "on-feature"), "feature/x")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def assertAllowed(self, command, cwd=None):
        self.assertIsNone(check(command, cwd or self.on_feature), command)

    def assertDenied(self, command, cwd=None):
        self.assertIsNotNone(check(command, cwd or self.on_feature), command)


class ExplicitTargets(RepoTestCase):
    def test_denies_pushes_to_protected_branches(self):
        for command in [
            "git push origin main",
            "git push origin master",
            "git push origin HEAD:main",
            "git push origin feature/x:main",
            "git push origin +main",
            "git push origin refs/heads/main",
            "git push origin :main",
            "git push origin 'main'",
            'git push origin "master"',
            "git push origin --delete main",
            "git push -f origin feature/x main",
            "git push -o ci.skip origin main",
            "git push origin -- main",
            "git -C sub push origin main",
            "git -c push.default=current push origin main",
            "/usr/bin/git push origin main",
            "FOO=1 git push origin main",
            "env git push origin main",
            'bash -c "git push origin main"',
            "git status\ngit push origin main",
            "git push \\\n  origin main",
            "true && git push origin main 2>&1 | tail -1",
        ]:
            self.assertDenied(command)

    def test_denies_pushes_of_every_branch(self):
        for command in ["git push --all origin", "git push --mirror", "git push --branches"]:
            self.assertDenied(command)

    def test_allows_feature_branch_pushes(self):
        for command in [
            "git push -u origin feature/x",
            "git push origin feature/main",
            "git push origin feature/maintenance-fix",
            "git push origin main-fix",
            "git push origin feature/x:feature/main",
            "git push -q -u origin feature/x 2>&1 | tail -1",
            "git push origin --tags",
        ]:
            self.assertAllowed(command)


class OtherCommandsMentioningMain(RepoTestCase):
    def test_ignores_main_outside_the_push_command(self):
        for command in [
            'git push -u origin feature/x; gh pr create --base main --title "t"',
            "git checkout -b x origin/main && git push -u origin x",
            "git log main..HEAD && git push origin feature/x",
            "git diff origin/main && git push origin feature/x",
            'git commit -m "git push origin main" && git push origin feature/x',
            'echo "git push origin main"',
            "git switch main",
            "git pull origin main",
        ]:
            self.assertAllowed(command)

    def test_ignores_heredoc_bodies(self):
        self.assertAllowed(
            "git push -u origin feature/x && gh pr create --base main --body-file - <<'EOF'\n"
            "Merge into main.\n"
            "git push origin main\n"
            "EOF"
        )
        self.assertAllowed(
            'git push -u origin feature/x; gh pr create --body "$(cat <<\'EOF\'\n'
            'It said "errors": 0 on main, and `it` isn\'t quoted.\n'
            "EOF\n"
            ')"'
        )

    def test_still_checks_commands_after_a_heredoc(self):
        self.assertDenied("cat <<EOF\nbody\nEOF\ngit push origin main")
        self.assertDenied("cat <<-EOF\nbody\n\tEOF\ngit push origin main")


class ImplicitTargets(RepoTestCase):
    def test_denies_implicit_push_from_main(self):
        for command in [
            "git push",
            "git push origin",
            "git push -u origin HEAD",
            "git push origin @",
            'git push origin "$(git branch --show-current)"',
        ]:
            self.assertDenied(command, cwd=self.on_main)

    def test_allows_implicit_push_from_feature_branch(self):
        for command in ["git push", "git push origin", "git push -u origin HEAD"]:
            self.assertAllowed(command, cwd=self.on_feature)

    def test_follows_cd_and_git_dash_c(self):
        self.assertDenied(f"cd {self.on_main} && git push", cwd=self.on_feature)
        self.assertAllowed(f"cd {self.on_feature} && git push", cwd=self.on_main)
        self.assertDenied(f"git -C {self.on_main} push", cwd=self.on_feature)
        self.assertDenied("cd on-main && git push", cwd=self._tmp.name)

    def test_allows_tags_only_push_from_main(self):
        self.assertAllowed("git push origin --tags", cwd=self.on_main)


class Unparseable(RepoTestCase):
    def test_denies_unparseable_push_mentioning_main(self):
        self.assertDenied("git push origin main 'unterminated")

    def test_allows_unparseable_command_without_push(self):
        self.assertAllowed("echo 'unterminated main")


class HookProtocol(RepoTestCase):
    def run_hook(self, command):
        payload = json.dumps({"tool_input": {"command": command}, "cwd": self.on_feature})
        return subprocess.run(
            [sys.executable, "-I", HOOK], input=payload, capture_output=True, text=True, check=True
        ).stdout

    def test_deny_emits_permission_decision(self):
        decision = json.loads(self.run_hook("git push origin main"))["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")

    def test_allow_emits_nothing(self):
        self.assertEqual(self.run_hook("git push origin feature/x"), "")


if __name__ == "__main__":
    unittest.main()
