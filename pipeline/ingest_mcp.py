"""Converts Telegram and Slack MCP results to the corpus format.

When an MCP result is too large for the conversation, Claude Code saves it to a file
(``{"result": "<json string>"}``). Reading those files keeps the text byte for byte, which
matters here: commas, letter case and "ё" are exactly what the profile measures.

Usage:
    python -m pipeline.ingest_mcp telegram --manifest dump.json [-o out.jsonl]
    python -m pipeline.ingest_mcp slack --manifest raw/en/manifest.json --owner U123 [-o ...]

Telegram manifest: a JSON list of ``{"chat_id", "name", "type", "file"}``; entries without
a file are skipped. Slack manifest: ``{"channel_id", "name", "kind", "pages", "threads"}``
where pages and thread files hold the plain-text ``messages`` of ``slack_read_channel`` /
``slack_read_thread`` (paths relative to the manifest), or ``saved_path`` points at a
result Claude Code saved.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from pipeline import corpus, ingest_slack
from pipeline.corpus import Message


def chat_type(mcp_type: str) -> str:
    """Maps the MCP chat type ("User", "Group (Basic)", "Supergroup", "Channel")."""
    low = mcp_type.lower()
    if low == "user":
        return "private"
    if "group" in low:
        return "group"
    if "channel" in low:
        return "channel"
    return low or "unknown"


def load_results(path: Path) -> list[dict[str, Any]]:
    """Reads the message list from a saved tool result.

    Raises:
        ValueError: The file is not a saved ``get_history`` result.
    """
    try:
        outer = json.loads(path.read_text(encoding="utf-8"))
        inner = json.loads(outer["result"]) if isinstance(outer.get("result"), str) else outer
        results = inner["results"]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{path}: not a saved get_history result ({exc})") from exc
    if not isinstance(results, list):
        raise ValueError(f"{path}: 'results' is not a list")
    return results


def parse_telegram(
    results: list[dict[str, Any]], chat_id: str, chat_name: str, mcp_type: str
) -> list[Message]:
    """Turns ``get_history`` records into messages, oldest first.

    Service events, forwards and records without text (stickers, voice, bare media)
    are skipped. A chat with yourself becomes chat type ``saved``.
    """
    own_ids = {str(r.get("sender_id")) for r in results if r.get("out")}
    kind = "saved" if chat_id in own_ids else chat_type(mcp_type)
    messages = []
    for r in results:
        text = r.get("text")
        if "action" in r or "forwarded" in r or not isinstance(text, str) or not text.strip():
            continue
        reply = r.get("reply_to")
        messages.append(
            Message(
                platform="telegram",
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type=kind,
                msg_id=str(r["id"]),
                ts=corpus.iso_from_text(r["date"]),
                author_id=str(r.get("sender_id", "")),
                author_name=str(r.get("sender") or ""),
                is_owner=bool(r.get("out")),
                text=text.strip(),
                reply_to=str(reply) if reply is not None else None,
            )
        )
    messages.sort(key=lambda m: m.dt)  # type: ignore[arg-type,return-value]
    return messages


_SLACK_HEADER = re.compile(
    r"^=== Message from (?P<name>.*?) (?:<[^>\n]*> )?\((?P<uid>[A-Z][A-Z0-9]+)(?:,[^)\n]*)?\) at "
    r"(?P<date>[^\n]*?) ===[ \t]*$",
    re.M,
)
_SLACK_TS = re.compile(r"^Message TS: (?P<ts>\d+\.\d+)[ \t]*$")
_SLACK_META = re.compile(
    r"^(?:Reactions: [\w+:-]+ \(\d+\)(?:, [\w+:-]+ \(\d+\))*"
    r"|Files: .*\(ID: F[A-Z0-9]+.*\)"
    r"|(?:Thread replies|Thread|Replies|Reply count): \d+.*)[ \t]*$"
)
SLACK_KINDS = {"dm": "private", "im": "private", "mpim": "group", "channel": "group"}


def slack_messages_text(path: Path) -> str:
    """Returns the plain ``messages`` text from a page file or a saved tool result."""
    raw = path.read_text(encoding="utf-8")
    if not raw.lstrip().startswith("{"):
        return raw
    try:
        data = json.loads(raw)
        if isinstance(data.get("result"), str):
            data = json.loads(data["result"])
        return str(data["messages"])
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{path}: not a Slack messages dump ({exc})") from exc


def parse_slack_text(text: str) -> list[dict[str, str]]:
    """Splits ``slack_read_channel``/``slack_read_thread`` text into records.

    Each record has ``uid``, ``name``, ``ts`` and ``text``. Trailing reaction, file and
    thread lines are metadata, not part of the message.
    """
    records = []
    headers = list(_SLACK_HEADER.finditer(text))
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        lines = text[h.end() : end].strip("\n").split("\n")
        if not lines or not (m := _SLACK_TS.match(lines[0])):
            continue
        body = lines[1:]
        while body and (not body[-1].strip() or _SLACK_META.match(body[-1])):
            body.pop()
        records.append(
            {"uid": h["uid"], "name": h["name"], "ts": m["ts"], "text": "\n".join(body).strip()}
        )
    return records


def slack_to_messages(
    records: list[dict[str, str]],
    channel_id: str,
    name: str,
    kind: str,
    owner: str,
    parent_ts: str | None = None,
) -> list[Message]:
    """Turns parsed records into messages; thread replies point at their parent."""
    result = []
    for r in records:
        if not r["text"]:
            continue
        reply = parent_ts if parent_ts and r["ts"] != parent_ts else None
        result.append(
            Message(
                platform="slack",
                chat_id=channel_id,
                chat_name=name,
                chat_type=SLACK_KINDS.get(kind, "group"),
                msg_id=r["ts"],
                ts=corpus.iso_from_epoch(float(r["ts"])),
                author_id=r["uid"],
                author_name=r["name"],
                is_owner=r["uid"] == owner,
                text=ingest_slack.render(r["text"], {}),
                reply_to=reply,
            )
        )
    return result


def load_slack_manifest(manifest: Path, owner: str) -> list[Message]:
    """Reads every page and thread listed in a Slack manifest."""
    base = manifest.parent
    messages: list[Message] = []
    for conv in json.loads(manifest.read_text(encoding="utf-8")):
        cid, name, kind = conv["channel_id"], conv.get("name", conv["channel_id"]), conv["kind"]
        sources: list[tuple[Path, str | None]] = [(base / p, None) for p in conv.get("pages", [])]
        for t in conv.get("threads", []):
            path = t.get("saved_path") or (base / t["file"] if t.get("file") else None)
            if path:
                sources.append((Path(path), t.get("parent_ts")))
        for path, parent in sources:
            try:
                records = parse_slack_text(slack_messages_text(path))
            except (OSError, ValueError) as exc:
                print(f"skip {path}: {exc}", file=sys.stderr)
                continue
            messages.extend(slack_to_messages(records, cid, name, kind, owner, parent))
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert saved Telegram MCP results.")
    sub = parser.add_subparsers(dest="source", required=True)
    tg = sub.add_parser("telegram")
    tg.add_argument("--manifest", type=Path, required=True)
    tg.add_argument("-o", "--out", type=Path, default=corpus.DEFAULT_DIR / "telegram-mcp.jsonl")
    sl = sub.add_parser("slack")
    sl.add_argument("--manifest", type=Path, required=True, nargs="+")
    sl.add_argument("--owner", required=True, help="your Slack user id")
    sl.add_argument("-o", "--out", type=Path, default=corpus.DEFAULT_DIR / "slack-mcp.jsonl")
    args = parser.parse_args(argv)

    if args.source == "slack":
        slack: list[Message] = []
        for path in args.manifest:
            try:
                slack.extend(load_slack_manifest(path, args.owner))
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                print(f"{path}: {exc}", file=sys.stderr)
                return 2
        corpus.write(slack, args.out)
        print(corpus.summary(slack, args.out))
        return 0

    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args.manifest}: {exc}", file=sys.stderr)
        return 2
    messages: list[Message] = []
    for entry in manifest:
        if not entry.get("file"):
            continue
        try:
            results = load_results(Path(entry["file"]))
        except (OSError, ValueError) as exc:
            print(f"skip {entry.get('name')}: {exc}", file=sys.stderr)
            continue
        messages.extend(
            parse_telegram(results, str(entry["chat_id"]), str(entry["name"]), entry["type"])
        )
    corpus.write(messages, args.out)
    print(corpus.summary(messages, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
