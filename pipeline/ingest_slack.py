"""Converts a Slack workspace export (zip or unpacked folder) to the common corpus format.

A standard export holds public channels only. Direct messages and private channels
appear when a workspace owner exports them on a paid plan.

Usage:
    python -m pipeline.ingest_slack export.zip --owner U012345 [-o out.jsonl]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from pipeline import corpus
from pipeline.corpus import Message

# Subtypes that still carry text the author typed.
KEEP_SUBTYPES = {None, "thread_broadcast", "me_message", "file_share"}

_TOKEN = re.compile(r"<([^<>]+)>")


class _Export:
    """Read-only view of an export, zip or directory, under a common root prefix."""

    def __init__(self, path: Path) -> None:
        self._zip: zipfile.ZipFile | None = None
        self._dir: Path | None = None
        if path.is_dir():
            self._dir = path
            names = [p.relative_to(path).as_posix() for p in path.rglob("*.json")]
        else:
            self._zip = zipfile.ZipFile(path)
            names = self._zip.namelist()
        users = [n for n in names if PurePosixPath(n).name == "users.json"]
        if not users:
            raise ValueError(f"{path}: users.json not found, is this a Slack export?")
        self.root = str(PurePosixPath(min(users, key=len)).parent)
        self.root = "" if self.root == "." else self.root + "/"
        self.names = names

    def load(self, name: str, default: Any = None) -> Any:
        full = self.root + name
        if full not in self.names:
            return default
        if self._zip is not None:
            return json.loads(self._zip.read(full).decode("utf-8"))
        assert self._dir is not None
        return json.loads((self._dir / full).read_text(encoding="utf-8"))

    def day_files(self, folder: str) -> list[str]:
        prefix = f"{self.root}{folder}/"
        return sorted(
            n[len(self.root) :]
            for n in self.names
            if n.startswith(prefix) and n.endswith(".json") and "/" not in n[len(prefix) :]
        )


def _user_name(user: dict[str, Any]) -> str:
    profile = user.get("profile") or {}
    return str(
        user.get("real_name")
        or profile.get("real_name")
        or profile.get("display_name")
        or user.get("name")
        or user.get("id", "")
    )


def render(text: str, handles: dict[str, str]) -> str:
    def token(match: re.Match[str]) -> str:
        body = match.group(1)
        target, _, label = body.partition("|")
        if target.startswith("@"):
            return "@" + (label.lstrip("@") or handles.get(target[1:], target[1:]))
        if target.startswith("#"):
            return "#" + (label or target[1:])
        if target.startswith("!"):
            return "@" + (label.lstrip("@") or target[1:].split("^")[0])
        return label or target

    return html.unescape(_TOKEN.sub(token, text))


def _resolve_owner(owner: str, users: list[dict[str, Any]], authors: Counter[str]) -> str:
    for user in users:
        profile = user.get("profile") or {}
        names = {
            user.get("id"),
            user.get("name"),
            user.get("real_name"),
            profile.get("real_name"),
            profile.get("display_name"),
        }
        if owner in names:
            return str(user["id"])
    lookup = {u.get("id"): _user_name(u) for u in users}
    raise corpus.owner_error((a, lookup.get(a, ""), n) for a, n in authors.most_common(5))


def parse(path: Path, owner: str) -> list[Message]:
    """Reads an export and returns messages from every channel and conversation in it.

    Join/leave events, bot posts and other system subtypes are skipped. Slack markup
    is rendered to plain text: mentions become ``@handle``, links keep their label.

    Args:
        path: The export zip or its unpacked directory.
        owner: Owner's Slack user id, handle, real name or display name.

    Returns:
        Messages, conversation by conversation, in time order.

    Raises:
        OwnerNotFoundError: ``owner`` matches no user in ``users.json``.
    """
    export = _Export(path)
    users: list[dict[str, Any]] = export.load("users.json", [])
    names = {str(u["id"]): _user_name(u) for u in users}
    handles = {str(u["id"]): str(u.get("name") or _user_name(u)) for u in users}

    convs: list[tuple[str, str, str, str]] = []
    for meta_file, chat_type in (("channels.json", "group"), ("groups.json", "group")):
        for ch in export.load(meta_file, []):
            convs.append((ch["name"], str(ch["id"]), ch["name"], chat_type))
    for ch in export.load("mpims.json", []):
        convs.append((ch["name"], str(ch["id"]), ch["name"], "group"))
    dms = export.load("dms.json", [])

    raw: list[tuple[str, str, str, dict[str, Any]]] = []
    for folder, chat_id, chat_name, chat_type in convs:
        for day in export.day_files(folder):
            raw.extend((chat_id, chat_name, chat_type, m) for m in export.load(day, []))
    dm_members: dict[str, list[str]] = {}
    for dm in dms:
        dm_members[str(dm["id"])] = [str(x) for x in dm.get("members", [])]
        for day in export.day_files(str(dm["id"])):
            raw.extend((str(dm["id"]), "", "private", m) for m in export.load(day, []))

    raw = [
        r
        for r in raw
        if r[3].get("type") == "message"
        and r[3].get("subtype") in KEEP_SUBTYPES
        and not r[3].get("bot_id")
        and r[3].get("user")
    ]
    owner_id = _resolve_owner(owner, users, Counter(str(r[3]["user"]) for r in raw))

    result: list[Message] = []
    for chat_id, chat_name, chat_type, msg in sorted(raw, key=lambda r: (r[0], float(r[3]["ts"]))):
        text = render(str(msg.get("text", "")), handles).strip()
        if not text:
            continue
        if chat_type == "private" and not chat_name:
            others = [m for m in dm_members.get(chat_id, []) if m != owner_id]
            chat_name = ", ".join(names.get(m, m) for m in others) or chat_id
        author = str(msg["user"])
        thread = msg.get("thread_ts")
        result.append(
            Message(
                platform="slack",
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type=chat_type,
                msg_id=str(msg["ts"]),
                ts=corpus.iso_from_epoch(float(msg["ts"])),
                author_id=author,
                author_name=names.get(author, author),
                is_owner=author == owner_id,
                text=text,
                reply_to=str(thread) if thread and thread != msg["ts"] else None,
            )
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert a Slack export to the corpus format.")
    parser.add_argument("export", type=Path, help="export zip or unpacked folder")
    parser.add_argument("--owner", required=True, help="your user id, handle or name")
    parser.add_argument(
        "-o", "--out", type=Path, default=corpus.DEFAULT_DIR / "slack.jsonl", help="output JSONL"
    )
    args = parser.parse_args(argv)
    try:
        messages = parse(args.export, owner=args.owner)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(exc, file=sys.stderr)
        return 2
    corpus.write(messages, args.out)
    print(corpus.summary(messages, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
