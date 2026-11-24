#!/usr/bin/env bash
# Installs the humanize skill and its prompt hook into Claude Code.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install.sh [--profile NAME] [--no-hook] [--uninstall]

Links skill/ as ~/.claude/skills/humanize ($CLAUDE_CONFIG_DIR/skills/humanize if set)
and registers hooks/draft_trigger.sh as a UserPromptSubmit hook in settings.json.
An existing humanize skill that is not this one is moved to backups/, outside skills/.
settings.json is backed up before it changes.

  --profile NAME  use profiles/NAME as the active profile (links skill/profile to it)
  --no-hook       do not touch settings.json
  --uninstall     remove the link and the hook; backups and profiles stay
EOF
}

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd -P)"
target="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
link="$target/skills/humanize"
hook_cmd="$repo/hooks/draft_trigger.sh"
stamp="$(date +%Y%m%d-%H%M%S)-$$"

mode=install
profile=""
with_hook=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      profile="${2:?--profile needs a name}"
      shift 2
      ;;
    --no-hook)
      with_hook=0
      shift
      ;;
    --uninstall)
      mode=uninstall
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

# Adds or removes our hook entry; other hooks are left as they are.
edit_settings() {
  local action=$1 settings="$target/settings.json"
  if [[ -f "$settings" ]]; then
    cp -p -- "$settings" "$settings.bak-$stamp"
    echo "backup: $settings.bak-$stamp"
  fi
  python3 - "$settings" "$hook_cmd" "$action" <<'PY'
import json
import shlex
import sys
from pathlib import Path

path, script, action = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
# Claude Code runs the command through a shell, so a path with spaces must be quoted.
command = shlex.quote(script)
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
groups = data.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
for group in groups:
    group["hooks"] = [h for h in group.get("hooks", []) if h.get("command") not in (command, script)]
groups[:] = [g for g in groups if g.get("hooks")]
if action == "add":
    groups.append({"hooks": [{"type": "command", "command": command}]})
if not groups:
    del data["hooks"]["UserPromptSubmit"]
if not data["hooks"]:
    del data["hooks"]
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

if [[ "$mode" == uninstall ]]; then
  if [[ -L "$link" && "$(readlink -f "$link")" == "$repo/skill" ]]; then
    rm -- "$link"
    echo "removed: $link"
  fi
  [[ -f "$target/settings.json" ]] && edit_settings remove
  exit 0
fi

if [[ -n "$profile" ]]; then
  if [[ ! "$profile" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]]; then
    echo "bad profile name: $profile" >&2
    exit 2
  fi
  if [[ ! -f "$repo/profiles/$profile/profile.md" ]]; then
    echo "profiles/$profile/profile.md not found; run: python3 -m pipeline.build_profile --profile profiles/$profile" >&2
    exit 1
  fi
  if [[ -e "$repo/skill/profile" && ! -L "$repo/skill/profile" ]]; then
    echo "skill/profile exists and is not a link; move it away first" >&2
    exit 1
  fi
  ln -sfn "../profiles/$profile" "$repo/skill/profile"
  echo "profile: profiles/$profile"
fi

mkdir -p "$target/skills"
if [[ -L "$link" && "$(readlink -f "$link")" == "$repo/skill" ]]; then
  echo "skill already linked: $link"
else
  if [[ -e "$link" || -L "$link" ]]; then
    mkdir -p "$target/backups"
    mv -- "$link" "$target/backups/humanize-$stamp"
    echo "moved old skill to: $target/backups/humanize-$stamp"
  fi
  ln -s "$repo/skill" "$link"
  echo "linked: $link -> $repo/skill"
fi

if [[ $with_hook -eq 1 ]]; then
  edit_settings add
  echo "hook: UserPromptSubmit -> $hook_cmd"
fi
echo "Restart running Claude Code sessions to pick it up."
