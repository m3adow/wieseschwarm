#!/usr/bin/env python3
"""PreToolUse hook (Bash matcher): deny any `git push` that targets main/master.
This repo requires PRs -- see root CLAUDE.md "Branch and PR conventions"."""

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED = {"main", "master"}
OPERATOR_CHARS = "();<>|&\n"
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
COMMAND_PREFIXES = {"env", "command", "time", "nohup", "exec"}
SHELLS = {"bash", "sh", "zsh"}
GIT_OPTS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
PUSH_OPTS_WITH_ARG = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
PUSH_ALL_BRANCHES = {"--all", "--branches", "--mirror"}

DENY_TARGET = (
    "Blocked: this command pushes to main/master directly. This repo requires PRs -- "
    "create a feature branch and open a PR instead."
)
DENY_ALL = (
    "Blocked: --all/--branches/--mirror would push main/master too. Push the feature "
    "branch by name and open a PR instead."
)
DENY_IMPLICIT = (
    "Blocked: currently on '{branch}' with no explicit push target -- this would push "
    "to {branch}. Create a feature branch and open a PR instead."
)
DENY_UNPARSEABLE = (
    "Blocked: could not parse this command, and it mentions push and main/master. "
    "Run the git push as a separate command."
)


def strip_heredoc_bodies(command):
    lines, pending = [], []
    for line in command.split("\n"):
        if pending:
            dash, word = pending[0]
            if (line.lstrip("\t") if dash else line) == word:
                pending.pop(0)
            continue
        lines.append(line)
        pending = [(m.group(1), m.group(3)) for m in HEREDOC.finditer(line)]
    return "\n".join(lines)


def tokenize(command):
    lexer = shlex.shlex(
        command.replace("\\\n", " "), posix=True, punctuation_chars=OPERATOR_CHARS
    )
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def split_commands(tokens):
    commands, current, skip_next = [], [], False
    for tok in tokens:
        if skip_next:
            skip_next = False
        elif tok and set(tok) <= set(OPERATOR_CHARS):
            if "<" in tok or ">" in tok:
                if current and current[-1].isdigit():
                    current.pop()
                skip_next = True
            else:
                if current:
                    commands.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        commands.append(current)
    return commands


def strip_prefixes(words):
    while words and (ASSIGNMENT.match(words[0]) or words[0] in COMMAND_PREFIXES):
        words = words[1:]
    return words


def change_dir(cwd, args):
    targets = [a for a in args if not a.startswith("-") or a == "-"]
    target = targets[0] if targets else "~"
    if target == "-":
        return cwd
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(target)))


def current_branch(cwd):
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def check_push(args, cwd):
    positional, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            positional += args[i + 1 :]
            break
        if arg in PUSH_ALL_BRANCHES:
            return DENY_ALL
        if arg in PUSH_OPTS_WITH_ARG:
            i += 2
            continue
        if not arg.startswith("-"):
            positional.append(arg)
        i += 1

    refspecs = positional[1:]
    if not refspecs:
        if "--tags" in args:
            return None
        branch = current_branch(cwd)
        return DENY_IMPLICIT.format(branch=branch) if branch in PROTECTED else None

    for spec in refspecs:
        dst = spec.lstrip("+").rpartition(":")[2].removeprefix("refs/heads/")
        if dst in ("HEAD", "@") or "$" in dst or "`" in dst:
            dst = current_branch(cwd)
        if dst in PROTECTED:
            return DENY_TARGET
    return None


def check_git(args, cwd):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in GIT_OPTS_WITH_ARG and i + 1 < len(args):
            if args[i] == "-C":
                cwd = change_dir(cwd, [args[i + 1]])
            i += 2
        else:
            i += 1
    if i < len(args) and args[i] == "push":
        return check_push(args[i + 1 :], cwd)
    return None


def check(command, cwd, depth=0):
    try:
        tokens = tokenize(strip_heredoc_bodies(command))
    except ValueError:
        if re.search(r"\bpush\b", command) and re.search(r"\b(main|master)\b", command):
            return DENY_UNPARSEABLE
        return None

    for words in split_commands(tokens):
        words = strip_prefixes(words)
        if not words:
            continue
        name = os.path.basename(words[0])
        reason = None
        if name in ("cd", "pushd"):
            cwd = change_dir(cwd, words[1:])
        elif name == "git":
            reason = check_git(words[1:], cwd)
        elif name in SHELLS and "-c" in words[1:-1] and depth < 3:
            reason = check(words[words.index("-c", 1) + 1], cwd, depth + 1)
        if reason:
            return reason
    return None


def main():
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command") or ""
    reason = check(command, payload.get("cwd") or os.getcwd())
    if reason:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
        )


if __name__ == "__main__":
    main()
