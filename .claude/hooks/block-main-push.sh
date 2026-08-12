#!/usr/bin/env bash
# PreToolUse hook (Bash matcher): deny any `git push` that targets main/master.
# This repo requires PRs -- see root CLAUDE.md "Branch and PR conventions".
input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty')"

# Strip quoted string contents (commit messages, PR bodies, -m/-b args, etc.) before
# pattern-matching, so text that merely *mentions* "git push"/"main" inside a quoted
# argument -- e.g. `git commit -m "add a git push guard for main"` -- isn't mistaken
# for an actual invocation. Tradeoff: a real push with a quoted branch name like
# `git push origin 'main'` would be missed; Claude never quotes plain branch names,
# so this favors avoiding false positives on everyday commands over that rare case.
#
# The `:a;N;$!ba` prefix slurps the whole (possibly multi-line) input into a
# single pattern space before matching. Without it, sed applies s/// per line,
# so a quoted argument whose closing quote is several lines below its opening
# quote -- any real commit message with a blank-line-separated body -- never
# gets matched at all, and ordinary prose anywhere in that body (e.g. a commit
# message that happens to say "convention on main") leaks through unstripped
# and can trip the word-boundary match below. Confirmed the hard way: this
# exact hook's own commit message did that to itself.
stripped="$(printf '%s' "$cmd" | sed -E ":a;N;\$!ba; s/'[^']*'/''/g; s/\"[^\"]*\"/\"\"/g")"

case "$stripped" in
  *git\ push*) : ;;
  *) exit 0 ;;
esac

deny() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}' "$1"
  exit 0
}

# Word-boundary match, not substring: "main"/"master" must stand alone as a ref
# token (bounded by start/space/colon on the left, space/colon/end on the right)
# so branch names that merely *contain* main/master as a path fragment --
# feature/main, feature/block-direct-push-to-main, feature/maintenance-fix --
# are not mistaken for the protected branch itself. `/` is deliberately NOT a
# left-boundary character (a prior version treated it as one and wrongly
# blocked `git push origin feature/main`); the full `refs/heads/main` /
# `refs/remotes/<remote>/main` forms are matched by the second alternative
# instead, which requires that exact prefix rather than any slash.
if printf '%s' "$stripped" | grep -qE '(^|[[:space:]:])(main|master)([[:space:]:]|$)' \
   || printf '%s' "$stripped" | grep -qE '(^|[[:space:]:])refs/(heads|remotes/[^/[:space:]]+)/(main|master)([[:space:]:]|$)'; then
  deny "Blocked: this command pushes to main/master directly. This repo requires PRs -- create a feature branch and open a PR instead."
fi

branch="$(git branch --show-current 2>/dev/null)"
case "$branch" in
  main|master)
    # Determine whether the push already names an explicit target (any
    # non-flag argument after "push" besides the remote itself -- a plain
    # branch name like `origin feature/x`, or a `src:dst` refspec). If so,
    # it's an explicit push and (since it didn't match main/master above)
    # safe to allow regardless of current branch. Only a bare `git push` or
    # `git push <remote>` implicitly pushes the current branch.
    #
    # Truncate at the first command separator so a chained command
    # (`git push origin main && ./deploy.sh`) doesn't pull unrelated tokens
    # into the count, then strip anything that looks like a remote URL
    # (`user@host:path` or `scheme://host[:port]/path`) so a colon that's
    # part of the remote spec -- not a refspec -- doesn't get counted as an
    # argument token or trip up the refspec check below.
    after_push="${stripped#*git push}"
    after_push="${after_push%%;*}"; after_push="${after_push%%&&*}"
    after_push="${after_push%%||*}"; after_push="${after_push%%|*}"
    no_urls="$(printf '%s' "$after_push" | sed -E 's#[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:[^[:space:]]*#REMOTE#g; s#[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]]*#REMOTE#g')"
    nonflag_count=0
    for tok in $no_urls; do
      case "$tok" in
        -*) ;;
        *) nonflag_count=$((nonflag_count + 1)) ;;
      esac
    done
    if [ "$nonflag_count" -le 1 ]; then
      deny "Blocked: currently on '$branch' with no explicit push target -- this would push to $branch. Create a feature branch and open a PR instead."
    fi
    ;;
esac

exit 0
