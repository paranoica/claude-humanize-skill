"""Blind-test report: lint metrics for generated and real replies, plus a page to guess.

Usage:
    python -m bench.report data/bench/<label> --profile profiles/me

Writes report.md (numbers, acceptance checks) and blind.html (pick the real reply out of
two) into the results directory. Both stay local: they quote other people's messages.
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

from bench.run import load_cases
from skill.scripts import lint

profilelib = lint.profilelib
tm = lint.tm

_BLOCK = re.compile(r"```[ \t]*\n(.*?)\n```[ \t]*", re.S)


def unwrap(text: str) -> str:
    """Joins a reply sent as one bare code block per message into one message per line.

    Only two or more blocks with nothing else around them count: a single message should
    be plain text, so a wrapped one is left for lint to flag, as is prose with a code block
    or a block with a language tag.
    """
    blocks = list(_BLOCK.finditer(text))
    if len(blocks) < 2 or _BLOCK.sub("", text).strip():
        return text
    return "\n".join(b.group(1).strip() for b in blocks)


def measure(text: str, rules: Any) -> dict[str, Any]:
    """Lint codes and raw numbers for one reply."""
    words = tm.words(text)
    issues = lint.check(text, rules, platform="plain", mode="chat")
    return {
        "codes": sorted({i.code for i in issues}),
        "errors": sum(i.level == "error" for i in issues),
        "warnings": sum(i.level == "warn" for i in issues),
        "words": len(words),
        "messages": len(lint.split_messages(text)),
        "commas": tm.count_commas(text),
        "yo": tm.count_yo_words(words),
    }


def collect(results: Path, profile: Path) -> list[dict[str, Any]]:
    """Joins every case with its generated reply and the metrics of both replies."""
    loaded = profilelib.load_profile(profile)
    rows = []
    for case in load_cases(profile / "holdout.jsonl"):
        path = results / f"case-{case.index:02d}.json"
        if not path.is_file():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("key") != case.key:
            record = {"error": "stale result from another holdout"}
        generated = unwrap(record.get("result") or "")
        real = "\n".join(case.reply)
        rules = loaded.rules(case.register) if case.register in loaded.registers else None
        rules = rules or profilelib.default_rules()
        rows.append(
            {
                "index": case.index,
                "register": case.register,
                "lang": case.lang,
                "context": case.context,
                "real": real,
                "generated": generated,
                "error": record.get("error"),
                "real_m": measure(real, rules),
                "gen_m": measure(generated, rules) if generated else None,
                "p90_words": rules.words_per_burst_p90,
                "p90_commas": rules.commas_rate_p90,
            }
        )
    return rows


def acceptance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The spec's numeric checks over the generated replies."""
    done = [r for r in rows if r["gen_m"]]
    return {
        "cases": len(rows),
        "answered": len(done),
        "no_yo": sum(r["gen_m"]["yo"] == 0 for r in done),
        "commas_within_p90": sum("commas" not in r["gen_m"]["codes"] for r in done),
        "lint_clean": sum(not r["gen_m"]["codes"] for r in done),
        "real_lint_clean": sum(not r["real_m"]["codes"] for r in rows),
    }


def render_report(rows: list[dict[str, Any]]) -> str:
    """Markdown summary with one line per case."""
    acc = acceptance(rows)
    n = acc["answered"]
    out = [
        "# Blind test report",
        "",
        f"Answered: {n} of {acc['cases']}.",
        f"- no 'ё': {acc['no_yo']}/{n}",
        f"- commas within the register's p90: {acc['commas_within_p90']}/{n}",
        f"- lint clean: {acc['lint_clean']}/{n} "
        f"(real replies: {acc['real_lint_clean']}/{acc['cases']})",
        "",
        "| # | register | words gen/real (p90) | msgs gen/real | lint gen | lint real |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        g, m = r["gen_m"], r["real_m"]
        gen_words = g["words"] if g else "-"
        gen_msgs = g["messages"] if g else "-"
        gen_codes = (", ".join(g["codes"]) or "clean") if g else (r["error"] or "missing")
        out.append(
            f"| {r['index']} | {r['register']} | {gen_words}/{m['words']} ({r['p90_words']}) | "
            f"{gen_msgs}/{m['messages']} | {gen_codes} | {', '.join(m['codes']) or 'clean'} |"
        )
    return "\n".join(out) + "\n"


def _bubbles(text: str) -> str:
    return "".join(
        f'<div class="b">{html.escape(line)}</div>' for line in text.splitlines() if line.strip()
    )


def render_blind(rows: list[dict[str, Any]], seed: int = 7) -> str:
    """A self-contained page: for each case pick which of two replies is real."""
    rng = random.Random(seed)
    cards, answers = [], {}
    for r in rows:
        if not r["generated"]:
            continue
        real_first = rng.random() < 0.5
        answers[r["index"]] = "A" if real_first else "B"
        a, b = (r["real"], r["generated"]) if real_first else (r["generated"], r["real"])
        ctx = "".join(
            f'<div class="b in">{html.escape(f"[{s}] {t}" if s else t)}</div>'
            for s, t in r["context"]
        )
        cards.append(
            f'<section data-i="{r["index"]}"><h2>#{r["index"]}</h2><div class="ctx">{ctx}</div>'
            f'<div class="opts"><div class="opt"><h3>A</h3>{_bubbles(a)}</div>'
            f'<div class="opt"><h3>B</h3>{_bubbles(b)}</div></div>'
            '<div class="pick">Настоящий: '
            '<button data-v="A">A</button><button data-v="B">B</button>'
            ' уверенность: <button data-c="1">наугад</button><button data-c="2">скорее</button>'
            '<button data-c="3">уверен</button><span class="res"></span></div></section>'
        )
    page = PAGE.replace("{{ANSWERS}}", json.dumps(answers))
    return page.replace("{{CARDS}}", "\n".join(cards))


PAGE = (Path(__file__).resolve().parent / "blind_template.html").read_text(encoding="utf-8")


def default_summary(results: Path, prompts: Path) -> str:
    """Lint of every default-mode answer against the no-profile rules."""
    from bench.run import load_prompts

    rules = profilelib.default_rules()
    lines = ["# Default mode", ""]
    clean = 0
    cases = load_prompts(prompts)
    for case in cases:
        path = results / f"case-{case.index:02d}.json"
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        text = unwrap(record.get("result") or "")
        issues = lint.check(text, rules, platform=case.platform, mode=case.mode) if text else []
        ok = bool(text) and not issues
        clean += ok
        codes = ", ".join(sorted({i.code for i in issues})) or ("clean" if text else "missing")
        lines.append(f"## {case.index}. {case.key} ({case.platform}, {case.mode}): {codes}")
        lines += ["", text or "(no answer)", ""]
    lines.insert(1, f"Lint clean: {clean}/{len(cases)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report on a blind-test run.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--prompts", type=Path, help="default-mode prompts file")
    args = parser.parse_args(argv)
    if args.prompts:
        text = default_summary(args.results, args.prompts)
        (args.results / "default.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    if args.profile is None:
        parser.error("give --profile or --prompts")
    try:
        rows = collect(args.results, args.profile)
    except profilelib.ProfileError as exc:
        print(exc, file=sys.stderr)
        return 2
    (args.results / "report.md").write_text(render_report(rows), encoding="utf-8")
    (args.results / "blind.html").write_text(render_blind(rows), encoding="utf-8")
    print(render_report(rows).split("\n\n| #")[0])
    print(f"-> {args.results / 'report.md'}\n-> {args.results / 'blind.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
