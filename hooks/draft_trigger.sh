#!/usr/bin/env bash
# Claude Code UserPromptSubmit hook, see draft_trigger.py. Exit code 2 would block the
# prompt, so any failure here exits 0 and the prompt goes through untouched.
script="$(dirname "${BASH_SOURCE[0]}")/draft_trigger.py"
[[ -f "$script" ]] || exit 0
python3 "$script" || exit 0
