"""Style statistics of the owner's messages, per register.

Usage:
    python -m pipeline.stats chats [--corpus data/corpus] [--json]
    python -m pipeline.stats compute [--corpus data/corpus] [--config corpus.toml] [-o stats.json]

``chats`` lists every chat with how much you wrote there, to decide what goes into
``corpus.toml``. ``compute`` measures each register: medians and 90th percentiles,
not just averages, because lint compares a draft against the owner's p90.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline import corpus, registers
from pipeline.corpus import Burst, Message
from skill.scripts import textmetrics as tm

TOP_FILLERS = 15
TOP_WORDS = 30
TOP_EMOJI = 10
ENDINGS = ("period", "ellipsis", "question", "exclamation", "bracket", "emoji", "none", "other")


def _share(part: float, whole: float) -> float | None:
    return round(part / whole, 3) if whole else None


def _round(dist: dict[str, Any]) -> dict[str, Any]:
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in dist.items()}


def assign_bursts(messages: list[Message], config: registers.Config) -> dict[str, list[Burst]]:
    """Splits owner messages into bursts and files each under its register."""
    result: dict[str, list[Burst]] = {name: [] for name in config.names}
    for chat in corpus.by_chat(messages).values():
        for burst in corpus.owner_bursts(chat, config.burst_gap_seconds):
            burst.lang = tm.detect_lang(burst.text)
            reg = config.assign(burst.first, burst.lang)
            if reg is not None:
                result[reg.name].append(burst)
    return result


def register_stats(bursts: list[Burst], min_words: int) -> dict[str, Any]:
    """Computes every style metric for one register's bursts.

    Args:
        bursts: The register's bursts.
        min_words: Bursts shorter than this are left out of the per-burst comma rate,
            where one comma in three words would read as 33 per 100.

    Returns:
        A JSON-ready dict of counts, shares and distributions.
    """
    msgs = [m for b in bursts for m in b.messages]
    msg_words = [tm.words(m.text) for m in msgs]
    all_words = [w for ws in msg_words for w in ws]
    n_words = len(all_words)
    n_msgs = len(msgs)

    burst_words = [sum(len(tm.words(m.text)) for m in b.messages) for b in bursts]
    burst_commas = [sum(tm.count_commas(m.text) for m in b.messages) for b in bursts]
    comma_rates = [
        100 * c / w for c, w in zip(burst_commas, burst_words, strict=True) if w >= min_words
    ]

    starts = [s for s in (tm.starts_upper(m.text) for m in msgs) if s is not None]
    endings = Counter(tm.ending(m.text) for m in msgs)
    n_ended = n_msgs - endings["empty"]

    cyr_words = [w for w in all_words if tm.detect_lang(w) == "ru"]
    yo_with, yo_total = tm.yo_known_counts(cyr_words)

    dashes = [tm.count_dashes(m.text) for m in msgs]
    negations = [tm.negation_counts(m.text) for m in msgs]
    contracted = sum(c for c, _ in negations)
    expanded = sum(e for _, e in negations)
    en_msgs = [m for m in msgs if tm.detect_lang(m.text) == "en"]

    lowered = [w.lower() for w in all_words]
    filler_set = tm.FILLERS["ru"] | tm.FILLERS["en"]
    fillers = Counter(w for w in lowered if w in filler_set)
    top_words = Counter(w for w in lowered if w not in tm.STOPWORDS and w not in filler_set)
    msg_emoji = [tm.emojis(m.text) for m in msgs]
    emoji_counter = Counter(e for es in msg_emoji for e in es)

    def per_100(count: int) -> float | None:
        return round(100 * count / n_words, 3) if n_words else None

    return {
        "n_messages": n_msgs,
        "n_bursts": len(bursts),
        "n_words": n_words,
        "n_chats": len({b.first.chat_key for b in bursts}),
        "words_per_message": _round(tm.distribution([len(ws) for ws in msg_words])),
        "chars_per_message": _round(tm.distribution([len(m.text) for m in msgs])),
        "words_per_burst": _round(tm.distribution(burst_words)),
        "messages_per_burst": _round(tm.distribution([len(b.messages) for b in bursts])),
        "multi_message_burst_share": _share(sum(len(b.messages) > 1 for b in bursts), len(bursts)),
        "commas": {
            "per_100_words": per_100(sum(burst_commas)),
            "per_100_words_per_burst": _round(tm.distribution(comma_rates)),
            "per_burst": _round(tm.distribution(burst_commas)),
        },
        "yo": {
            "word_share": _share(tm.count_yo_words(cyr_words), len(cyr_words)),
            "known_with_yo": yo_with,
            "known_total": yo_total,
        },
        "capital_start_share": _share(sum(starts), len(starts)),
        "ending": {k: _share(endings[k], n_ended) for k in ENDINGS},
        "dashes": {
            "per_100_words": per_100(sum(dashes)),
            "message_share": _share(sum(d > 0 for d in dashes), n_msgs),
        },
        "en": {
            "messages": len(en_msgs),
            "apostrophe_message_share": _share(
                sum(tm.has_apostrophe(m.text) for m in en_msgs), len(en_msgs)
            ),
            "negation_contraction_share": _share(contracted, contracted + expanded),
            "negations": contracted + expanded,
        },
        "fillers": [
            {"word": w, "count": c, "per_100_words": per_100(c)}
            for w, c in fillers.most_common(TOP_FILLERS)
        ],
        "top_words": [{"word": w, "count": c} for w, c in top_words.most_common(TOP_WORDS)],
        "emoji": {
            "message_share": _share(sum(bool(es) for es in msg_emoji), n_msgs),
            "per_100_messages": (
                round(100 * sum(emoji_counter.values()) / n_msgs, 3) if n_msgs else None
            ),
            "top": [{"emoji": e, "count": c} for e, c in emoji_counter.most_common(TOP_EMOJI)],
        },
        "bracket_smiley_message_share": _share(
            sum(tm.bracket_smileys(m.text) > 0 for m in msgs), n_msgs
        ),
        "profanity_message_share": _share(sum(tm.has_profanity(m.text) for m in msgs), n_msgs),
    }


def _register_lang(config: registers.Config, name: str, bursts: list[Burst]) -> str:
    declared = next(r.lang for r in config.registers if r.name == name)
    if declared:
        return declared
    counts = Counter(b.lang for b in bursts if b.lang in registers.LANGS)
    return counts.most_common(1)[0][0] if counts else "ru"


def compute(messages: list[Message], config: registers.Config) -> dict[str, Any]:
    """Statistics for every register that has at least one burst."""
    by_register = assign_bursts(messages, config)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "burst_gap_seconds": config.burst_gap_seconds,
        "min_words_for_rate": config.min_words_for_rate,
        "registers": {
            name: {"lang": _register_lang(config, name, bursts)}
            | register_stats(bursts, config.min_words_for_rate)
            for name, bursts in by_register.items()
            if bursts
        },
    }


def list_chats(messages: list[Message]) -> list[dict[str, Any]]:
    """One row per chat: volume, how much of it is yours and in which language.

    Rows are sorted by your message count, most active first.
    """
    rows: list[dict[str, Any]] = []
    for chat in corpus.by_chat(messages).values():
        first = chat[0]
        own = [m for m in chat if m.is_owner]
        langs = Counter(tm.detect_lang(m.text) for m in own)
        dates = sorted(m.ts[:10] for m in chat if m.ts)
        rows.append(
            {
                "platform": first.platform,
                "chat_type": first.chat_type,
                "chat_id": first.chat_id,
                "chat_name": first.chat_name,
                "messages": len(chat),
                "owner_messages": len(own),
                "owner_ru_share": _share(langs["ru"], len(own)),
                "owner_en_share": _share(langs["en"], len(own)),
                "first": dates[0] if dates else None,
                "last": dates[-1] if dates else None,
            }
        )
    rows.sort(key=lambda r: r["owner_messages"], reverse=True)
    return rows


def _print_chats(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'yours':>6} {'total':>6} {'ru%':>4} {'en%':>4}  "
        f"{'platform':<8} {'type':<7} {'id':<14} name"
    )
    for r in rows:
        ru = f"{100 * r['owner_ru_share']:.0f}" if r["owner_ru_share"] is not None else "-"
        en = f"{100 * r['owner_en_share']:.0f}" if r["owner_en_share"] is not None else "-"
        print(
            f"{r['owner_messages']:>6} {r['messages']:>6} {ru:>4} {en:>4}  "
            f"{r['platform']:<8} {r['chat_type']:<7} {r['chat_id']:<14} {r['chat_name']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Style statistics of your messages.")
    sub = parser.add_subparsers(dest="command", required=True)
    chats = sub.add_parser("chats", help="list chats with your message counts")
    chats.add_argument("--corpus", type=Path, default=corpus.DEFAULT_DIR)
    chats.add_argument("--json", action="store_true", help="print JSON instead of a table")
    comp = sub.add_parser("compute", help="measure each register")
    comp.add_argument("--corpus", type=Path, default=corpus.DEFAULT_DIR)
    comp.add_argument(
        "--config", type=Path, help="corpus.toml; without it, one register per language"
    )
    comp.add_argument("-o", "--out", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)

    if not args.corpus.exists():
        print(f"{args.corpus}: not found, run an ingest script first", file=sys.stderr)
        return 2
    messages = corpus.read(args.corpus)

    if args.command == "chats":
        rows = list_chats(messages)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _print_chats(rows)
        return 0

    try:
        config = registers.load(args.config) if args.config else registers.default_config()
    except registers.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    result = compute(messages, config)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        for name, reg in result["registers"].items():
            print(f"{name}: {reg['n_messages']} messages, {reg['n_bursts']} turns")
        print(f"-> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
