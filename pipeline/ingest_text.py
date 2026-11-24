"""Brings any other texts into the common corpus format.

Two inputs are accepted:

* ``.jsonl``: one message per line. Only ``text`` is required. Optional fields:
  ``is_owner``, ``author``, ``ts`` (ISO 8601), ``chat``, ``chat_type``, ``id``,
  ``reply_to``. A line is yours if ``is_owner`` is true or ``author`` equals
  ``--owner-name``. The chat defaults to the file name.
* ``.txt`` / ``.md``: your own writing (posts, emails, notes). Each paragraph, split
  on blank lines, becomes one message of chat type ``text``.

Usage:
    python -m pipeline.ingest_text FILE [FILE ...] [--owner-name NAME] [-o out.jsonl]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from pipeline import corpus
from pipeline.corpus import Message

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _file_tag(path: Path) -> str:
    # Line numbers repeat across files that share a chat; the tag keeps ids apart
    # while re-ingesting the same file still dedupes.
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:8]


def _parse_jsonl(path: Path, owner_name: str | None) -> list[Message]:
    result: list[Message] = []
    tag = _file_tag(path)
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: line {lineno}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("text"), str):
            raise ValueError(f"{path}: line {lineno}: a string field 'text' is required")
        author = str(row.get("author") or "")
        is_owner = bool(row.get("is_owner")) or (owner_name is not None and author == owner_name)
        chat = str(row.get("chat") or path.stem)
        reply = row.get("reply_to")
        result.append(
            Message(
                platform="text",
                chat_id=chat,
                chat_name=chat,
                chat_type=str(row.get("chat_type") or "private"),
                msg_id=str(row["id"]) if row.get("id") is not None else f"{tag}:{lineno}",
                ts=corpus.iso_from_text(row["ts"]) if row.get("ts") else None,
                author_id=author or ("owner" if is_owner else "other"),
                author_name=author,
                is_owner=is_owner,
                text=row["text"].strip(),
                reply_to=str(reply) if reply is not None else None,
            )
        )
    return result


def _parse_prose(path: Path) -> list[Message]:
    tag = _file_tag(path)
    paragraphs = [p.strip() for p in _PARAGRAPH_BREAK.split(path.read_text(encoding="utf-8"))]
    return [
        Message(
            platform="text",
            chat_id=path.stem,
            chat_name=path.stem,
            chat_type="text",
            msg_id=f"{tag}:{i}",
            ts=None,
            author_id="owner",
            author_name="",
            is_owner=True,
            text=p,
        )
        for i, p in enumerate((p for p in paragraphs if p), start=1)
    ]


def parse(path: Path, owner_name: str | None = None) -> list[Message]:
    """Reads one ``.jsonl``, ``.txt`` or ``.md`` file.

    Args:
        path: Input file.
        owner_name: Author name that marks your lines in a ``.jsonl`` file.

    Returns:
        Messages in file order.

    Raises:
        ValueError: A ``.jsonl`` line is not an object with a string ``text``, or the
            file type is not supported.
    """
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _parse_jsonl(path, owner_name)
    if suffix in {".txt", ".md"}:
        return _parse_prose(path)
    raise ValueError(f"{path}: unsupported file type {suffix!r}, expected .jsonl, .txt or .md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert plain texts to the corpus format.")
    parser.add_argument("files", type=Path, nargs="+")
    parser.add_argument("--owner-name", help="author name that marks your lines in .jsonl")
    parser.add_argument(
        "-o", "--out", type=Path, default=corpus.DEFAULT_DIR / "text.jsonl", help="output JSONL"
    )
    args = parser.parse_args(argv)
    messages: list[Message] = []
    try:
        for path in args.files:
            messages.extend(parse(path, owner_name=args.owner_name))
    except (ValueError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 2
    corpus.write(messages, args.out)
    print(corpus.summary(messages, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
