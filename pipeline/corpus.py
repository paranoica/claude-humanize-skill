"""Common corpus format: one JSON object per message, one JSONL file per source."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DIR = Path("data/corpus")


class OwnerNotFoundError(ValueError):
    """The owner of the export could not be identified."""


@dataclass(frozen=True)
class Message:
    """A single chat message in the common format.

    Attributes:
        platform: Source, e.g. ``telegram``, ``slack``, ``text``.
        chat_id: Stable chat identifier within the platform.
        chat_name: Human-readable chat name: a person, a group or a channel.
        chat_type: ``private``, ``group``, ``channel``, ``bot``, ``saved`` or ``text``.
        msg_id: Message identifier within the chat.
        ts: ISO 8601 timestamp in UTC, or ``None`` when the source has no time.
        author_id: Author identifier within the platform.
        author_name: Author display name.
        is_owner: True if the profile owner wrote the message.
        text: Plain message text.
        reply_to: ``msg_id`` of the message this one replies to, if known.
    """

    platform: str
    chat_id: str
    chat_name: str
    chat_type: str
    msg_id: str
    ts: str | None
    author_id: str
    author_name: str
    is_owner: bool
    text: str
    reply_to: str | None = None

    @property
    def chat_key(self) -> tuple[str, str]:
        return (self.platform, self.chat_id)

    @property
    def dt(self) -> datetime | None:
        return datetime.fromisoformat(self.ts) if self.ts else None


@dataclass
class Burst:
    """Consecutive owner messages in one chat sent close together: one thought, one turn.

    Attributes:
        messages: The owner's messages, in order.
        index: Position of the first message in its chat's message list.
    """

    messages: list[Message]
    index: int
    lang: str = field(default="other")

    @property
    def text(self) -> str:
        return "\n".join(m.text for m in self.messages)

    @property
    def first(self) -> Message:
        return self.messages[0]


def iso_from_epoch(seconds: float) -> str:
    """Formats a Unix timestamp as ISO 8601 in UTC."""
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def iso_from_text(value: str) -> str:
    """Normalises an ISO 8601 string to UTC; naive values are taken as UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def write(messages: Iterable[Message], path: Path) -> int:
    """Writes messages to a JSONL file, creating parent directories.

    Returns:
        Number of messages written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for msg in messages:
            fh.write(json.dumps(dataclasses.asdict(msg), ensure_ascii=False) + "\n")
            n += 1
    return n


def _iter_file(path: Path) -> Iterator[Message]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield Message(**json.loads(line))


def read(source: Path) -> list[Message]:
    """Reads a JSONL file or every ``*.jsonl`` in a directory.

    Duplicates (same platform, chat and message id, e.g. from overlapping exports) are
    dropped, keeping the first. Messages are grouped by chat in first-seen order and
    sorted by time within a chat when every message there has a timestamp.

    Args:
        source: A ``.jsonl`` file or a directory with them.

    Returns:
        The messages, chat by chat.
    """
    files = sorted(source.glob("*.jsonl")) if source.is_dir() else [source]
    seen: set[tuple[str, str, str]] = set()
    chats: dict[tuple[str, str], list[Message]] = {}
    for path in files:
        for msg in _iter_file(path):
            key = (msg.platform, msg.chat_id, msg.msg_id)
            if key in seen:
                continue
            seen.add(key)
            chats.setdefault(msg.chat_key, []).append(msg)
    result: list[Message] = []
    for msgs in chats.values():
        if all(m.ts for m in msgs):
            msgs = sorted(msgs, key=lambda m: m.dt)  # type: ignore[arg-type,return-value]
        result.extend(msgs)
    return result


def by_chat(messages: Iterable[Message]) -> dict[tuple[str, str], list[Message]]:
    """Groups messages by chat, keeping their order."""
    chats: dict[tuple[str, str], list[Message]] = {}
    for msg in messages:
        chats.setdefault(msg.chat_key, []).append(msg)
    return chats


def owner_bursts(chat: list[Message], gap_seconds: float) -> list[Burst]:
    """Splits the owner's messages in one chat into bursts.

    A burst ends when someone else writes, or when the pause before the next owner
    message is longer than ``gap_seconds``. Messages without a timestamp never join.

    Args:
        chat: Messages of a single chat, in order.
        gap_seconds: Longest pause that still continues a burst.

    Returns:
        Bursts in chat order.
    """
    bursts: list[Burst] = []
    current: Burst | None = None
    for i, msg in enumerate(chat):
        if not msg.is_owner:
            current = None
            continue
        if current is not None:
            prev = current.messages[-1].dt
            now = msg.dt
            if prev and now and (now - prev).total_seconds() <= gap_seconds:
                current.messages.append(msg)
                continue
        current = Burst(messages=[msg], index=i)
        bursts.append(current)
    return bursts


def summary(messages: list[Message], out: Path) -> str:
    """One line for the ingest scripts: how much was read and where it went."""
    owned = sum(m.is_owner for m in messages)
    chats = len({m.chat_key for m in messages})
    return f"{len(messages)} messages ({owned} yours) from {chats} chats -> {out}"


def owner_error(candidates: Iterable[tuple[str, str, int]]) -> OwnerNotFoundError:
    """Builds an error that lists the most active authors, to help pick the owner."""
    lines = [f"  {aid}  {name}  ({n} messages)" for aid, name, n in candidates]
    return OwnerNotFoundError(
        "cannot tell which author is you; pass the owner explicitly. Most active authors:\n"
        + "\n".join(lines)
    )
