"""Blind-test runner: the skill answers held-out messages through `claude -p`.

Each job gets a throwaway HOME with only this skill (plus the profile, minus the holdout
and corpus.toml), the prompt hook and an empty MCP config, so the user's own CLAUDE.md,
plugins and servers never reach the answer. The OAuth credentials file is symlinked, not
copied: a refresh in a copy would rotate the token and log out the real session.

Usage:
    python -m bench.run --profile profiles/me [--label NAME] [--jobs 4] [--only 1,5]
                        [--no-profile] [--with-claude-md] [--model NAME]

Results go to data/bench/<label>/ (git-ignored: they hold other people's messages).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skill"
WORK_ROOT = Path.home() / ".cache" / "claude-humanize-skill" / "jobs"
REAL_CLAUDE = Path.home() / ".claude"
EMPTY_MCP = '{"mcpServers":{}}'
TOOLS = "Skill,Read,Write,Bash"
ALLOWED = ["Skill", "Read", "Write", "Bash(python3:*)"]
PROFILE_FILES = ("profile.md", "settings.toml", "stats.json", "registers")
TURN_TIMEOUT_S = 600
ATTEMPTS = 2

PLATFORM = {
    "telegram": ("в Telegram", "on Telegram"),
    "slack": ("в Slack", "on Slack"),
}

_credentials_lock = threading.Lock()


@dataclass(frozen=True)
class Case:
    """One held-out exchange."""

    index: int
    key: str
    register: str
    lang: str
    platform: str
    context: list[tuple[str, str]]
    reply: list[str]
    prompt: str | None = None
    mode: str = "chat"


def load_cases(holdout: Path) -> list[Case]:
    """Reads holdout.jsonl written by build_profile."""
    cases = []
    for i, line in enumerate(holdout.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        h = json.loads(line)
        cases.append(
            Case(
                index=i,
                key=h["key"],
                register=h["register"],
                lang=h["lang"],
                platform=h["platform"],
                context=[(s, t) for s, t in h["context"]],
                reply=list(h["reply"]),
            )
        )
    return cases


def load_prompts(path: Path) -> list[Case]:
    """Reads a list of ready prompts ({id, prompt, platform, mode}) for the default-mode test."""
    items = json.loads(path.read_text(encoding="utf-8"))
    return [
        Case(
            index=i,
            key=item["id"],
            register="default",
            lang="ru" if any("а" <= ch <= "я" for ch in item["prompt"].lower()) else "en",
            platform=item.get("platform", "plain"),
            context=[],
            reply=[],
            prompt=item["prompt"],
            mode=item.get("mode", "chat"),
        )
        for i, item in enumerate(items, start=1)
    ]


def build_prompt(case: Case, with_register: bool = True) -> str:
    """The request a user would type: answer this as me, here is what they wrote."""
    if case.prompt is not None:
        return case.prompt
    ru = case.lang == "ru"
    where = PLATFORM.get(case.platform, ("", ""))[0 if ru else 1]
    lines = [f"[{s}] {t}" if s else t for s, t in case.context]
    if ru:
        head = f"Ответь от моего имени {where}".strip() + "."
        if with_register:
            head += f" Регистр: {case.register}."
        return f"{head}\n\nВходящее:\n" + "\n".join(lines)
    head = f"Reply as me {where}".strip() + "."
    if with_register:
        head += f" Register: {case.register}."
    return f"{head}\n\nIncoming:\n" + "\n".join(lines)


def make_home(
    job_dir: Path,
    profile: Path | None,
    credentials: Path,
    claude_md: Path | None = None,
) -> tuple[Path, Path]:
    """Creates an isolated HOME with the skill, its profile and the prompt hook.

    Returns:
        ``(home, cwd)``.
    """
    home, cwd = job_dir / "home", job_dir / "cwd"
    claude_dir = home / ".claude"
    skill_dir = claude_dir / "skills" / "humanize"
    claude_dir.mkdir(parents=True)
    cwd.mkdir(parents=True)
    (claude_dir / ".credentials.json").symlink_to(credentials)
    shutil.copytree(
        SKILL, skill_dir, ignore=shutil.ignore_patterns("profile", "__pycache__", "*.pyc")
    )
    if profile is not None:
        for name in PROFILE_FILES:
            src = profile / name
            if src.is_dir():
                shutil.copytree(src, skill_dir / "profile" / name)
            elif src.is_file():
                (skill_dir / "profile").mkdir(exist_ok=True)
                shutil.copyfile(src, skill_dir / "profile" / name)
    if claude_md is not None and claude_md.is_file():
        shutil.copyfile(claude_md, claude_dir / "CLAUDE.md")
    hook = REPO / "hooks" / "draft_trigger.sh"
    settings = {
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": str(hook)}]}]}
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    trust = {
        "hasCompletedOnboarding": True,
        "projects": {str(cwd): {"hasTrustDialogAccepted": True}},
    }
    (home / ".claude.json").write_text(json.dumps(trust), encoding="utf-8")
    return home, cwd


def build_command(prompt: str, model: str | None = None) -> list[str]:
    """`claude -p` argv. The prompt goes first: --tools and --allowedTools are variadic."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--strict-mcp-config",
        "--mcp-config",
        EMPTY_MCP,
        "--tools",
        TOOLS,
        "--allowedTools",
        *ALLOWED,
    ]
    if model:
        cmd += ["--model", model]
    return cmd


def clean_env(home: Path) -> dict[str, str]:
    """Child environment: no Claude/Anthropic variables, HOME pointed at the job."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC_"))}
    env["HOME"] = str(home)
    return env


def _token_expiry(raw: bytes) -> int | None:
    """``expiresAt`` of a credentials file, or None if it holds no usable token."""
    try:
        oauth = json.loads(raw).get("claudeAiOauth") or {}
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        return None
    if not oauth.get("accessToken"):
        return None
    try:
        return int(oauth.get("expiresAt") or 0)
    except (TypeError, ValueError):
        return 0


def sync_credentials(home: Path, credentials: Path) -> str | None:
    """Copies a refreshed token back if claude replaced the symlink with a real file.

    Only a valid token that expires later than the current one is copied, so an empty
    file or an older token from a slower job never replaces a good one.
    """
    link = home / ".claude" / ".credentials.json"
    if link.is_symlink() or not link.is_file():
        return None
    with _credentials_lock:
        fresh = link.read_bytes()
        fresh_exp = _token_expiry(fresh)
        if fresh_exp is None:
            return None
        current = credentials.read_bytes() if credentials.exists() else b""
        current_exp = _token_expiry(current)
        if current == fresh or (current_exp is not None and fresh_exp <= current_exp):
            return None
        dest = credentials.resolve()
        tmp = dest.with_name(f".credentials.{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(fresh)
        tmp.chmod(0o600)
        os.replace(tmp, dest)
    return f"credentials refreshed inside {home}; copied back"


def parse_output(stdout: str) -> dict[str, Any]:
    """Reads `claude -p --output-format stream-json` output.

    Keeps the reply, a few counters and the tool trail: which skills were loaded and
    whether lint ran, so a run shows that the hook and the skill actually worked.

    Raises:
        ValueError: There is no final result event.
    """
    tools: list[str] = []
    skills: list[str] = []
    lint_runs = 0
    similar_runs = 0
    hook_context = False
    denied: list[str] = []
    final: dict[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result":
            final = event
        if event.get("subtype") == "hook_response" and "additionalContext" in str(
            event.get("output")
        ):
            hook_context = True
        if event.get("subtype") == "permission_denied":
            denied.append(json.dumps(event, ensure_ascii=False)[:300])
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if event.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tools.append(block.get("name", ""))
            inp = block.get("input") or {}
            if block.get("name") == "Skill":
                skills.append(str(inp.get("skill") or inp.get("command") or ""))
            command = str(inp.get("command", "")) if block.get("name") == "Bash" else ""
            lint_runs += "lint.py" in command
            similar_runs += "similar.py" in command
    if final is None:
        raise ValueError(f"no result event: {stdout[-300:]!r}")
    return {
        "result": (final.get("result") or "").strip(),
        "is_error": bool(final.get("is_error")),
        "num_turns": final.get("num_turns"),
        "duration_ms": final.get("duration_ms"),
        "cost_usd": final.get("total_cost_usd"),
        "tools": tools,
        "skills": skills,
        "lint_runs": lint_runs,
        "similar_runs": similar_runs,
        "hook_context": hook_context,
        "denied": denied,
    }


@dataclass(frozen=True)
class Options:
    profile: Path | None
    model: str | None
    claude_md: Path | None
    with_register: bool


def run_case(case: Case, opts: Options, out: Path, credentials: Path) -> str | None:
    """Runs one case (with one retry) and writes its JSON record. Returns an error or None."""
    error: str | None = None
    record: dict[str, Any] = {}
    prompt = build_prompt(case, opts.with_register)
    for _ in range(ATTEMPTS):
        job_dir = WORK_ROOT / f"case{case.index}-{uuid.uuid4().hex[:8]}"
        try:
            home, cwd = make_home(job_dir, opts.profile, credentials, opts.claude_md)
            proc = subprocess.run(
                build_command(prompt, opts.model),
                cwd=cwd,
                env=clean_env(home),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=TURN_TIMEOUT_S,
                check=False,
            )
            if proc.returncode != 0:
                error = f"exit {proc.returncode}: {proc.stderr[-400:]}"
                continue
            record = parse_output(proc.stdout)
            error = None if record["result"] and not record["is_error"] else "empty or error result"
            if error is None:
                break
        except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            sync_credentials(job_dir / "home", credentials)
            shutil.rmtree(job_dir, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"index": case.index, "key": case.key, "register": case.register, "prompt": prompt}
    data.update(record)
    data["error"] = error
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the humanize blind test.")
    parser.add_argument("--profile", type=Path, help="profile with holdout.jsonl")
    parser.add_argument("--prompts", type=Path, help="ready prompts instead of the holdout")
    parser.add_argument("--label", default=datetime.now().strftime("%Y%m%d-%H%M"))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only", default="", help="comma-separated case numbers")
    parser.add_argument("--model")
    parser.add_argument("--no-profile", action="store_true", help="skill in default mode")
    parser.add_argument("--no-register", action="store_true", help="do not name the register")
    parser.add_argument(
        "--with-claude-md", action="store_true", help="also install the real ~/.claude/CLAUDE.md"
    )
    args = parser.parse_args(argv)

    credentials = REAL_CLAUDE / ".credentials.json"
    if not credentials.is_file():
        print(f"no credentials at {credentials}; log in with `claude` first", file=sys.stderr)
        return 1
    if args.prompts is None and args.profile is None:
        parser.error("give --profile or --prompts")
    cases = (
        load_prompts(args.prompts) if args.prompts else load_cases(args.profile / "holdout.jsonl")
    )
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        cases = [c for c in cases if c.index in wanted]
    opts = Options(
        profile=None if args.no_profile or args.profile is None else args.profile,
        model=args.model,
        claude_md=REAL_CLAUDE / "CLAUDE.md" if args.with_claude_md else None,
        with_register=not args.no_register,
    )
    results = REPO / "data" / "bench" / args.label
    results.mkdir(parents=True, exist_ok=True)
    meta = {
        "profile": str(args.profile) if args.profile else None,
        "prompts": str(args.prompts) if args.prompts else None,
        "no_profile": args.no_profile,
        "with_claude_md": args.with_claude_md,
        "with_register": not args.no_register,
        "model": args.model,
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    (results / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    pending = [c for c in cases if not _done(results / f"case-{c.index:02d}.json", c.key)]
    print(f"{len(pending)} of {len(cases)} cases to run -> {results}", flush=True)
    failed = 0
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(run_case, c, opts, results / f"case-{c.index:02d}.json", credentials): c
            for c in pending
        }
        for k, future in enumerate(as_completed(futures), 1):
            case = futures[future]
            error = future.result()
            failed += error is not None
            status = "ok" if error is None else f"FAIL {error[:200]}"
            print(
                f"[{k}/{len(pending)} {time.monotonic() - started:.0f}s] case {case.index} {status}"
            )
    source = f"--prompts {args.prompts}" if args.prompts else f"--profile {args.profile}"
    print(f"done, {failed} failed. Next: python -m bench.report {results} {source}")
    return 1 if failed else 0


def _done(path: Path, key: str) -> bool:
    """A finished, error-free result for this very case (not a stale one from an old holdout)."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return record.get("error") is None and record.get("key") == key


if __name__ == "__main__":
    raise SystemExit(main())
