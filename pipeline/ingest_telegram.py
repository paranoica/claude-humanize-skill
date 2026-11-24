"""Converts a Telegram Desktop JSON export (``result.json``) to the common corpus format.

Works with a full account export (Settings → Advanced → Export Telegram data, JSON)
and with a single-chat export. AyuGram and other Desktop forks write the same format.

Usage:
    python -m pipeline.ingest_telegram path/to/result.json [--owner-id 123] [-o out.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pipeline import corpus
from pipeline.corpus import Message

CHAT_TYPES = {
    "personal_chat": "private",
    "bot_chat": "bot",
    "saved_messages": "saved",
    "private_group": "group",
    "private_supergroup": "group",
    "public_supergroup": "group",
    "private_channel": "channel",
    "public_channel": "channel",
}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text", "")) for part in value
        )
    return ""


def _chats(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if "messages" in data:
        yield data
        return
    for section in ("chats", "left_chats"):
        yield from data.get(section, {}).get("list", [])


def _normalise_owner(owner_id: str | int) -> str:
    owner = str(owner_id)
    return owner if owner.startswith(("user", "channel")) else f"user{owner}"


def _ts(msg: dict[str, Any]) -> str | None:
    if msg.get("date_unixtime"):
        return corpus.iso_from_epoch(int(msg["date_unixtime"]))
    if msg.get("date"):
        return corpus.iso_from_text(msg["date"])
    return None


def parse(data: dict[str, Any], owner_id: str | int | None = None) -> list[Message]:
    """Turns a parsed export into messages.

    Service events, forwards and messages with no text (stickers, bare media) are
    skipped: they say nothing about how the owner writes.

    Args:
        data: The decoded ``result.json``.
        owner_id: Owner's Telegram user id, ``123`` or ``user123``. Read from
            ``personal_information`` in a full export.

    Returns:
        Messages, chat by chat, in export order.

    Raises:
        OwnerNotFoundError: No owner given and the export does not name one.
    """
    if owner_id is None:
        info = data.get("personal_information", {})
        if "user_id" not in info:
            authors: Counter[tuple[str, str]] = Counter()
            for chat in _chats(data):
                for msg in chat.get("messages", []):
                    if msg.get("type") == "message" and msg.get("from_id"):
                        authors[(msg["from_id"], str(msg.get("from") or ""))] += 1
            raise corpus.owner_error((a, n, c) for (a, n), c in authors.most_common(5))
    owner = _normalise_owner(owner_id if owner_id is not None else info["user_id"])

    result: list[Message] = []
    for chat in _chats(data):
        chat_type = CHAT_TYPES.get(chat.get("type", ""), chat.get("type", "unknown"))
        chat_id = str(chat.get("id", ""))
        chat_name = str(chat.get("name") or chat_id)
        for msg in chat.get("messages", []):
            if msg.get("type") != "message" or "forwarded_from" in msg:
                continue
            text = _text(msg.get("text")).strip()
            if not text:
                continue
            author = str(msg.get("from_id", ""))
            reply = msg.get("reply_to_message_id")
            result.append(
                Message(
                    platform="telegram",
                    chat_id=chat_id,
                    chat_name=chat_name,
                    chat_type=chat_type,
                    msg_id=str(msg["id"]),
                    ts=_ts(msg),
                    author_id=author,
                    author_name=str(msg.get("from") or ""),
                    is_owner=author == owner,
                    text=text,
                    reply_to=str(reply) if reply is not None else None,
                )
            )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a Telegram Desktop export to the corpus format."
    )
    parser.add_argument("export", type=Path, help="result.json from Telegram Desktop")
    parser.add_argument("--owner-id", help="your Telegram user id, if the export lacks it")
    parser.add_argument(
        "-o", "--out", type=Path, default=corpus.DEFAULT_DIR / "telegram.jsonl", help="output JSONL"
    )
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.export.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args.export}: cannot read export: {exc}", file=sys.stderr)
        return 2
    try:
        messages = parse(data, owner_id=args.owner_id)
    except corpus.OwnerNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    corpus.write(messages, args.out)
    print(corpus.summary(messages, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
