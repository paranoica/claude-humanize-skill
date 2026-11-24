"""Builds a profile from the corpus: stats, pair banks, facts and an index.

Usage:
    python -m pipeline.build_profile --profile profiles/me [--corpus data/corpus]

Reads ``corpus.toml`` (which chats, which registers) and ``settings.toml`` from the
profile directory and writes:

* ``stats.json``: style metrics per register;
* ``registers/<name>/facts.md``: the metrics as plain rules, in the register's language;
* ``registers/<name>/examples.md``: 30 to 50 real "incoming -> owner's reply" pairs;
* ``registers/<name>/guide.md``: a template for the voice guide, only if it is missing;
* ``profile.md``: the index the skill reads first;
* ``holdout.jsonl``: pairs kept out of the bank for the blind test. Selected once and
  reused on rebuilds, so they never leak into the bank.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline import corpus, registers, stats
from pipeline.corpus import Message
from skill.scripts import profilelib
from skill.scripts import textmetrics as tm

MAX_CONTEXT = 4
CONTEXT_WINDOW_S = 6 * 3600
MAX_CONTEXT_CHARS = 300
MAX_REPLY_WORDS = 250
HOLDOUT_NEIGHBOUR_S = 2 * 3600
MIN_HOLDOUT_PER_REGISTER = 2
MIN_HOLDOUT_CONTEXT_WORDS = 3
MAX_INITIATIONS = 5
MAX_POOL = 600

_URL = re.compile(r"(?:https?://|www\.)[^\s<>]*[^\s<>.,;:!?)\]'\"]")
_DOMAIN = re.compile(
    r"(?<![\w@.])(?:[a-z0-9-]+\.)+(?:com|ru|org|net|io|me|dev|app|co|su|ly|gg|tv|info|xyz|ai|so|to"
    r"|us|uk|de|рф)\b(?:/[^\s<>]*[^\s<>.,;:!?)\]'\"])?",
    re.IGNORECASE,
)
_LOCAL_PHONE = re.compile(r"(?<!\w)\d{3}-\d{2}-\d{2}(?!\w)")
# Keys and tokens: 16+ characters with letters and at least three digits. Passwords:
# lower case, upper case and a digit, and either 12+ characters or a symbol in them.
_SECRET = re.compile(
    r"(?<![\w-])(?:"
    r"(?=(?:[\w\-+/=.!@#$%^&*]*?\d){3})(?=[\w\-+/=.!@#$%^&*]*[A-Za-z])[\w\-+/=.!@#$%^&*]{16,}"
    r"|(?=\S*[a-z])(?=\S*[A-Z])(?=\S*\d)(?:(?=\S*[!@#$%^&*])[\x21-\x7e]{8,}|[\x21-\x7e]{12,})"
    r")(?![\w-])"
)
_CREDENTIAL = re.compile(
    r"(?i)(?:\b(?:pass(?:word)?|pwd|login|логин|пароль|пасс)\s*[:=]\s*\S+"
    r"|\b[\w.-]+:[\w.-]*pass[\w.-]*\b)"
)
# One-time codes only next to a word that says so; plain prices stay.
_CODE = re.compile(r"(?i)\b(?P<word>код|code|pin|пин|otp|sms|смс|кодик)(?P<sep>[\s:=-]*)\d{4,8}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(
    r"(?<![\w(])(?:(?:\+\d{1,3}|8)[ -]?)?\(?\d{2,4}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?!\w)"
)
_LONG_NUMBER = re.compile(r"\d{8,}")
_MASKS = {
    "ru": {
        "url": "<ссылка>",
        "email": "<почта>",
        "phone": "<телефон>",
        "number": "<число>",
        "secret": "<секрет>",
    },
    "en": {
        "url": "<link>",
        "email": "<email>",
        "phone": "<phone>",
        "number": "<number>",
        "secret": "<secret>",
    },
}

SETTINGS_TEMPLATE = """\
# Owner settings for the humanize skill. Every rule set to "auto" is decided from stats.json.

owner = ""
default_platform = "plain"   # plain, slack, telegram, discord, email

[rules]
yo = "auto"              # auto, never, allow: the letter "ё"
dashes = "auto"          # auto, never, allow: em/en dashes and " - " used as a dash
emoji = "auto"           # auto, never, allow
en_apostrophes = "auto"  # auto, never, allow: contractions like "don't" in English
profanity = "mirror"     # never, mirror (only if the other side swears), allow

[stoplist]
ru = []
en = []

# Per-register overrides, e.g.:
# [registers.friends]
# emoji = "allow"
"""


@dataclass
class Pair:
    """One owner turn with the messages it answered.

    Attributes:
        key: ``platform:chat_id:first_msg_id`` of the reply, stable across rebuilds.
        context: ``(speaker, text)`` of the incoming messages, oldest first. Speaker is
            empty in a private chat and ``A``, ``B``... in a group.
        reply: The owner's messages in the turn.
    """

    key: str
    register: str
    lang: str
    platform: str
    chat_id: str
    chat_type: str
    ts: str | None
    context: list[tuple[str, str]]
    reply: list[str]
    n_words: int = field(default=0)


def mask(text: str, lang: str) -> str:
    """Replaces links, emails, phone numbers and long numbers with neutral labels."""
    labels = _MASKS["ru" if lang == "ru" else "en"]
    text = _URL.sub(labels["url"], text)
    text = _CREDENTIAL.sub(labels["secret"], text)
    text = _SECRET.sub(labels["secret"], text)
    text = _EMAIL.sub(labels["email"], text)
    text = _DOMAIN.sub(labels["url"], text)
    text = _CODE.sub(lambda m: m["word"] + m["sep"] + labels["number"], text)
    text = _PHONE.sub(lambda m: labels["number" if m[0].isdigit() else "phone"], text)
    text = _LOCAL_PHONE.sub(labels["phone"], text)
    return _LONG_NUMBER.sub(labels["number"], text)


def _context(
    chat: list[Message], index: int, start: datetime | None, lang: str
) -> list[tuple[str, str]]:
    picked: list[Message] = []
    i = index - 1
    while i >= 0 and not chat[i].is_owner and len(picked) < MAX_CONTEXT:
        msg = chat[i]
        if start and msg.dt and (start - msg.dt).total_seconds() > CONTEXT_WINDOW_S:
            break
        picked.append(msg)
        i -= 1
    picked.reverse()
    group = chat[index].chat_type != "private"
    letters: dict[str, str] = {}
    result = []
    for msg in picked:
        speaker = ""
        if group:
            speaker = letters.setdefault(msg.author_id, chr(ord("A") + len(letters) % 26))
        text = mask(msg.text, lang)
        if len(text) > MAX_CONTEXT_CHARS:
            text = text[:MAX_CONTEXT_CHARS].rstrip() + "…"
        result.append((speaker, text))
    return result


def extract_pairs(messages: list[Message], config: registers.Config) -> list[Pair]:
    """Turns every owner turn that falls into a register into a pair.

    Turns with no words (links, stickers) or longer than ``MAX_REPLY_WORDS`` are left
    out: they teach nothing about how the owner writes a message.
    """
    pairs: list[Pair] = []
    for chat in corpus.by_chat(messages).values():
        for burst in corpus.owner_bursts(chat, config.burst_gap_seconds):
            lang = tm.detect_lang(burst.text)
            reg = config.assign(burst.first, lang)
            if reg is None:
                continue
            reply = [mask(m.text, lang) for m in burst.messages]
            n_words = sum(len(tm.words(r)) for r in reply)
            if not 1 <= n_words <= MAX_REPLY_WORDS:
                continue
            first = burst.first
            pairs.append(
                Pair(
                    key=f"{first.platform}:{first.chat_id}:{first.msg_id}",
                    register=reg.name,
                    lang=reg.lang or lang,
                    platform=first.platform,
                    chat_id=first.chat_id,
                    chat_type=first.chat_type,
                    ts=first.ts,
                    context=_context(chat, burst.index, first.dt, lang),
                    reply=reply,
                    n_words=n_words,
                )
            )
    return pairs


def _allocate[K](sizes: dict[K, int], total: int) -> dict[K, int]:
    """Splits ``total`` across groups in proportion to size (largest remainder)."""
    pool = sum(sizes.values())
    if pool == 0 or total <= 0:
        return dict.fromkeys(sizes, 0)
    total = min(total, pool)
    exact = {k: total * v / pool for k, v in sizes.items()}
    alloc = {k: min(int(x), sizes[k]) for k, x in exact.items()}
    rest = sorted(sizes, key=lambda k: exact[k] - int(exact[k]), reverse=True)
    i = 0
    while sum(alloc.values()) < total:
        k = rest[i % len(rest)]
        if alloc[k] < sizes[k]:
            alloc[k] += 1
        i += 1
    return alloc


def select_holdout(pairs: list[Pair], n: int, seed: int) -> list[Pair]:
    """Picks ``n`` answered pairs across registers.

    Every register gets at least ``MIN_HOLDOUT_PER_REGISTER`` (or all it has), so a
    small register is still tested; the rest is split in proportion to size.
    """
    # A masked label like <ссылка> in the real reply would give it away in a blind test.
    labels = {v for lang in _MASKS.values() for v in lang.values()}
    answered = [
        p
        for p in pairs
        if sum(len(tm.words(t)) for _, t in p.context) >= MIN_HOLDOUT_CONTEXT_WORDS
        and not any(lb in r for r in p.reply for lb in labels)
    ]
    by_reg: dict[str, list[Pair]] = {}
    for p in answered:
        by_reg.setdefault(p.register, []).append(p)
    rng = random.Random(seed)
    base = {r: min(MIN_HOLDOUT_PER_REGISTER, len(v)) for r, v in by_reg.items()}
    while sum(base.values()) > n:
        base[max(base, key=lambda r: base[r])] -= 1
    rest = _allocate({r: len(v) - base[r] for r, v in by_reg.items()}, n - sum(base.values()))
    chosen: list[Pair] = []
    for reg, group in by_reg.items():
        chosen.extend(rng.sample(group, base[reg] + rest[reg]))
    return chosen


def _bucket(pair: Pair) -> tuple[int, bool, bool]:
    size = 0 if pair.n_words <= 3 else 1 if pair.n_words <= 10 else 2 if pair.n_words <= 25 else 3
    asked = bool(pair.context) and "?" in pair.context[-1][1]
    return size, len(pair.reply) > 1, asked


def _norm_reply(pair: Pair) -> str:
    return " ".join(w.lower() for w in tm.words(" ".join(pair.reply)))


def select_bank(pairs: list[Pair], n: int, seed: int) -> list[Pair]:
    """Picks up to ``n`` varied pairs that still look like the owner's usual mix.

    Pairs are grouped by reply length, single or multi-message, and whether the last
    incoming message was a question. Each group gets a share proportional to its size,
    so the bank keeps the owner's real proportions instead of over-sampling rare long
    replies. Repeated replies are dropped, and one chat supplies at most a quarter of
    the bank unless there are not enough pairs elsewhere.
    """
    seen: set[str] = set()
    unique = []
    for p in pairs:
        norm = _norm_reply(p)
        if norm and norm not in seen:
            seen.add(norm)
            unique.append(p)
    rng = random.Random(seed)
    groups: dict[tuple[int, bool, bool], list[Pair]] = {}
    for p in unique:
        groups.setdefault(_bucket(p), []).append(p)
    for g in groups.values():
        rng.shuffle(g)
    alloc = _allocate({k: len(v) for k, v in groups.items()}, n)
    chat_cap = max(3, n // 4)
    per_chat: dict[str, int] = {}
    chosen: list[Pair] = []
    leftovers: list[Pair] = []
    for key, group in groups.items():
        taken = 0
        for p in group:
            if taken < alloc[key] and per_chat.get(p.chat_id, 0) < chat_cap:
                chosen.append(p)
                per_chat[p.chat_id] = per_chat.get(p.chat_id, 0) + 1
                taken += 1
            else:
                leftovers.append(p)
    # Fill up from the leftovers: other chats first, then whatever is left, so a
    # register built from one or two chats still gets a full bank.
    for respect_cap in (True, False):
        for p in leftovers:
            if len(chosen) >= min(n, len(unique)):
                break
            if p in chosen or (respect_cap and per_chat.get(p.chat_id, 0) >= chat_cap):
                continue
            chosen.append(p)
            per_chat[p.chat_id] = per_chat.get(p.chat_id, 0) + 1
    chosen.sort(key=lambda p: (p.ts or "", p.key))
    return chosen


def write_pool(path: Path, pairs: list[Pair], seed: int, strip_yo: bool) -> int:
    """Writes up to ``MAX_POOL`` answered pairs for similar.py, one JSON object per line.

    Returns:
        How many pairs were written.
    """
    chosen = list(pairs)
    random.Random(seed).shuffle(chosen)
    chosen = sorted(chosen[:MAX_POOL], key=lambda p: (p.ts or "", p.key))
    with path.open("w", encoding="utf-8") as fh:
        for p in chosen:
            incoming = [f"[{s}] {t}" if s else t for s, t in p.context]
            if strip_yo:
                incoming = [t.replace("ё", "е").replace("Ё", "Е") for t in incoming]
            fh.write(json.dumps({"in": incoming, "out": p.reply}, ensure_ascii=False) + "\n")
    return len(chosen)


def near_holdout(pair: Pair, holdout: list[dict[str, Any]]) -> bool:
    """True if the pair must stay out of the bank because of the held-out set.

    That is the held-out pair itself, the same reply said again anywhere (people repeat
    themselves, and exports overlap), or a turn within two hours of a held-out one in
    the same chat.
    """
    norm = _norm_reply(pair)
    for h in holdout:
        if h["key"] == pair.key:
            return True
        if norm and norm == " ".join(w.lower() for w in tm.words(" ".join(h.get("reply", [])))):
            return True
        if h["platform"] == pair.platform and h["chat_id"] == pair.chat_id and h["ts"] and pair.ts:
            gap = abs(
                (datetime.fromisoformat(h["ts"]) - datetime.fromisoformat(pair.ts)).total_seconds()
            )
            if gap <= HOLDOUT_NEIGHBOUR_S:
                return True
    return False


# --- rendering -----------------------------------------------------------------

_CHAT_LABELS = {
    "ru": {
        "private": "личка",
        "group": "группа",
        "channel": "канал",
        "text": "текст",
        "bot": "бот",
    },
    "en": {"private": "dm", "group": "group", "channel": "channel", "text": "text", "bot": "bot"},
}


def _indent(text: str) -> str:
    return "\n".join(
        "  " + line if i == 0 else "    " + line for i, line in enumerate(text.splitlines())
    )


def render_examples(
    name: str, lang: str, bank: list[Pair], initiations: list[Pair], strip_yo: bool = False
) -> str:
    """Renders the pair bank as Markdown in the register's language.

    With ``strip_yo`` the letter is replaced in other people's messages too, so the
    model does not pick it up from the context of a register that never uses it.
    """
    ru = lang == "ru"
    labels = _CHAT_LABELS["ru" if ru else "en"]
    if ru:
        out = [
            f"# Примеры: {name}",
            "",
            f"{len(bank)} настоящих реплик владельца и то, на что был ответ. Это главный "
            "ориентир по голосу: длина, регистр букв, пунктуация, словечки, разбивка на "
            "сообщения. Писать так же, но не копировать фразы дословно.",
            "Каждая строка ответа это отдельное сообщение. Ссылки, почты и телефоны "
            "заменены метками.",
        ]
        incoming, answer, first = "Входящее:", "Ответ:", "Пишет первым"
    else:
        out = [
            f"# Examples: {name}",
            "",
            f"{len(bank)} real turns by the owner with what they answered. This is the main "
            "reference for the voice: length, letter case, punctuation, pet words, how a "
            "thought is split into messages. Write the same way; never copy lines verbatim.",
            "Each reply line is a separate message. Links, emails and phone numbers are "
            "replaced with labels.",
        ]
        incoming, answer, first = "Incoming:", "Reply:", "Writing first"
    for i, p in enumerate(bank, start=1):
        out += ["", f"## {i} · {labels.get(p.chat_type, p.chat_type)}"]
        if p.context:
            out.append(incoming)
            for speaker, text in p.context:
                if strip_yo:
                    text = text.replace("ё", "е").replace("Ё", "Е")
                out.append(_indent(f"[{speaker}] {text}" if speaker else text))
        out.append(answer)
        out.extend(_indent(r) for r in p.reply)
    if initiations:
        out += ["", f"## {first}"]
        for p in initiations:
            out.append("")
            out.extend(_indent(r) for r in p.reply)
    return "\n".join(out) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.0f}%"


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}" if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def _slov(value: float | None) -> str:
    """A number followed by the right form of "слово"."""
    if value is None:
        return "n/a"
    num = _num(value)
    if not num.isdigit():
        return f"{num} слова"
    n = int(num)
    if n % 10 == 1 and n % 100 != 11:
        return f"{num} слово"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{num} слова"
    return f"{num} слов"


_RULE_TEXT = {
    "ru": {
        "never": "нельзя",
        "allow": "можно",
        "yo": "Буква йо (е с точками)",
        "dashes": "Тире (длинное, короткое, дефис с пробелами)",
        "emoji": "Эмодзи",
        "en_apostrophes": "Апострофы и сокращения в английском",
        "profanity": {
            "never": "Мат: нельзя",
            "mirror": "Мат: только если собеседник сам матерится",
            "allow": "Мат: можно",
        },
    },
    "en": {
        "never": "never",
        "allow": "allowed",
        "yo": "The Russian letter yo",
        "dashes": "Dashes (em, en, spaced hyphen)",
        "emoji": "Emoji",
        "en_apostrophes": "Apostrophes and contractions",
        "profanity": {
            "never": "Profanity: never",
            "mirror": "Profanity: only if the other person swears",
            "allow": "Profanity: allowed",
        },
    },
}


def render_facts(name: str, reg: dict[str, Any], rules: profilelib.Rules, min_words: int) -> str:
    """Turns a register's stats and resolved rules into short readable facts."""
    lang = "ru" if reg.get("lang") == "ru" else "en"
    t = _RULE_TEXT[lang]
    hard_keys = ["yo", "dashes", "emoji"] if lang == "ru" else ["dashes", "emoji", "en_apostrophes"]
    hard = [f"- {t[k]}: {t[getattr(rules, k)]}" for k in hard_keys]
    yo = reg.get("yo", {})
    if lang == "ru" and yo.get("known_total"):
        hard[0] += f" (в корпусе {yo['known_with_yo']} из {yo['known_total']} слов, где она бывает)"
    hard.append(f"- {t['profanity'][rules.profanity]}")
    wb, mb, wm = reg["words_per_burst"], reg["messages_per_burst"], reg["words_per_message"]
    commas = reg["commas"]
    end = reg["ending"]
    fillers = ", ".join(f"{f['word']} {_num(f['per_100_words'])}" for f in reg["fillers"][:10])
    words = ", ".join(w["word"] for w in reg["top_words"][:20])
    emoji_top = " ".join(e["emoji"] for e in reg["emoji"]["top"][:5])
    if lang == "ru":
        lines = [
            f"# Факты: {name}",
            "",
            f"Посчитано скриптом по {reg['n_messages']} сообщениям ({reg['n_bursts']} реплик) "
            f"в {reg['n_chats']} чатах. Файл перезаписывается при сборке, свои правки "
            "вносить в guide.md. «Обычно» здесь медиана, «редко больше» это 90-й процентиль.",
            "",
            "## Жесткие правила (их проверяет lint)",
            "",
            *hard,
            "",
            "## Как выглядит реплика",
            "",
            "Реплика это все сообщения владельца подряд, пока не ответит собеседник.",
            "",
            f"- Длина реплики: обычно {_slov(wb['median'])}, редко больше {_num(wb['p90'])}.",
            f"- Сообщений в реплике: обычно {_num(mb['median'])}, редко больше "
            f"{_num(mb['p90'])}. Мысль разбита на несколько сообщений в "
            f"{_pct(reg['multi_message_burst_share'])} реплик.",
            f"- Одно сообщение: обычно {_slov(wm['median'])}, редко больше {_num(wm['p90'])}.",
            f"- Запятые: {_num(commas['per_100_words'])} на 100 слов. В реплике от {min_words} "
            f"слов редко больше {_num(commas['per_100_words_per_burst']['p90'])} на 100 слов, "
            f"в реплике целиком обычно {_num(commas['per_burst']['median'])}, редко больше "
            f"{_num(commas['per_burst']['p90'])}.",
            f"- С заглавной буквы начинается {_pct(reg['capital_start_share'])} сообщений.",
            f"- Конец сообщения: без знака {_pct(end['none'])}, точка {_pct(end['period'])}, "
            f"вопрос {_pct(end['question'])}, скобка {_pct(end['bracket'])}, многоточие "
            f"{_pct(end['ellipsis'])}, восклицание {_pct(end['exclamation'])}, эмодзи "
            f"{_pct(end['emoji'])}.",
            f"- Скобки-смайлы вроде «))» в {_pct(reg['bracket_smiley_message_share'])} сообщений.",
            f"- Эмодзи в {_pct(reg['emoji']['message_share'])} сообщений"
            + (f", чаще всего {emoji_top}." if emoji_top else "."),
            f"- Мат в {_pct(reg['profanity_message_share'])} сообщений.",
            "",
            "## Словечки",
            "",
            f"Частицы и слова-паразиты, на 100 слов: {fillers or 'нет'}.",
            "",
            f"Частые слова: {words or 'нет'}.",
        ]
    else:
        en = reg["en"]
        lines = [
            f"# Facts: {name}",
            "",
            f"Counted by a script over {reg['n_messages']} messages ({reg['n_bursts']} turns) in "
            f"{reg['n_chats']} chats. Rewritten on every build; put your own notes in guide.md. "
            "'Usually' is the median, 'rarely above' is the 90th percentile.",
            "",
            "## Hard rules (lint checks these)",
            "",
            *hard,
            "",
            "## What a turn looks like",
            "",
            "A turn is every message the owner sends in a row before the other side answers.",
            "",
            f"- Turn length: usually {_num(wb['median'])} words, rarely above {_num(wb['p90'])}.",
            f"- Messages per turn: usually {_num(mb['median'])}, rarely above {_num(mb['p90'])}. "
            f"{_pct(reg['multi_message_burst_share'])} of turns are split into several messages.",
            f"- One message: usually {_num(wm['median'])} words, rarely above {_num(wm['p90'])}.",
            f"- Commas: {_num(commas['per_100_words'])} per 100 words. In turns of {min_words}+ "
            f"words rarely above {_num(commas['per_100_words_per_burst']['p90'])} per 100 words; "
            f"per turn usually {_num(commas['per_burst']['median'])}, rarely above "
            f"{_num(commas['per_burst']['p90'])}.",
            f"- Starts with a capital letter: {_pct(reg['capital_start_share'])} of messages.",
            f"- Message endings: no mark {_pct(end['none'])}, period {_pct(end['period'])}, "
            f"question {_pct(end['question'])}, bracket {_pct(end['bracket'])}, ellipsis "
            f"{_pct(end['ellipsis'])}, exclamation {_pct(end['exclamation'])}, emoji "
            f"{_pct(end['emoji'])}.",
            *(
                [
                    f"- English negations contracted (don't vs do not): "
                    f"{_pct(en['negation_contraction_share'])} of {en['negations']}."
                ]
                if en["negations"]
                else []
            ),
            f"- Bracket smileys like '))': "
            f"{_pct(reg['bracket_smiley_message_share'])} of messages.",
            f"- Emoji: {_pct(reg['emoji']['message_share'])} of messages"
            + (f", mostly {emoji_top}." if emoji_top else "."),
            f"- Profanity: {_pct(reg['profanity_message_share'])} of messages.",
            "",
            "## Pet words",
            "",
            f"Fillers and particles, per 100 words: {fillers or 'none'}.",
            "",
            f"Frequent words: {words or 'none'}.",
        ]
    return "\n".join(lines) + "\n"


def render_guide_template(name: str, lang: str | None) -> str:
    """A starting point for the hand-written voice guide."""
    if lang == "ru":
        return (
            f"# Голос: {name}\n\n"
            "<!-- Этот файл пишется руками, сборка его не трогает. Опиши живыми словами, "
            "как ты пишешь здесь, и дай пару своих коротких фраз. -->\n\n"
            "## Кому\n\n\n## Как звучит\n\n\n## Типичные ходы\n\n\n## Чего не бывает\n\n"
        )
    return (
        f"# Voice: {name}\n\n"
        "<!-- Written by hand, the build never touches it. Describe in plain words how you "
        "write here and add a few of your own short lines. -->\n\n"
        "## Who\n\n\n## How it sounds\n\n\n## Typical moves\n\n\n## Never\n\n"
    )


def _applies_to(reg: registers.Register) -> str:
    parts = []
    if reg.platforms:
        parts.append(", ".join(sorted(reg.platforms)))
    if reg.chat_types:
        parts.append(", ".join(sorted(reg.chat_types)) + " chats")
    if reg.chats:
        parts.append("chats: " + ", ".join(sorted(reg.chats)))
    if reg.lang:
        parts.append(f"text in {reg.lang}")
    return "; ".join(parts) or "any chat"


def render_index(settings: dict[str, Any], config: registers.Config, built: dict[str, int]) -> str:
    """The profile index: settings in brief and how to choose a register."""
    owner = settings["owner"]
    out = [
        f"# Profile{': ' + owner if owner else ''}",
        "",
        "Generated by `build_profile.py`. Edit `settings.toml`, `corpus.toml` and each "
        "register's `guide.md`, then rebuild; do not edit this file.",
        "",
        f"Default platform: {settings['default_platform']}.",
        "",
        "## Registers",
        "",
        "Pick the register by who the text is for and where it goes. If two fit equally, "
        "ask one short question.",
    ]
    for reg in config.registers:
        if reg.name not in built:
            continue
        base = f"registers/{reg.name}"
        out += [
            "",
            f"### {reg.name}" + (f" ({reg.lang})" if reg.lang else ""),
            "",
            reg.description or "(no description in corpus.toml)",
            "",
            f"Applies to: {_applies_to(reg)}. Built from {built[reg.name]} turns.",
            "",
            f"Read: `{base}/guide.md`, `{base}/facts.md`, `{base}/examples.md`.",
            f"Similar replies: `python3 <skill-dir>/scripts/similar.py --register {reg.name}`"
            " with the incoming message on stdin.",
            f"Lint: `python3 <skill-dir>/scripts/lint.py --register {reg.name}`.",
        ]
    return "\n".join(out) + "\n"


# --- orchestration ---------------------------------------------------------------


def _load_holdout(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def build(
    profile_dir: Path,
    messages: list[Message],
    bank_size: int = 40,
    holdout_size: int = 20,
    seed: int = 13,
    reset_holdout: bool = False,
) -> dict[str, Any]:
    """Writes every generated file of a profile.

    Returns:
        A summary: per register, how many pairs were found and how many went to the bank.

    Raises:
        registers.ConfigError: ``corpus.toml`` is malformed.
        profilelib.ProfileError: ``settings.toml`` is malformed.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    corpus_toml = profile_dir / "corpus.toml"
    config = registers.load(corpus_toml) if corpus_toml.exists() else registers.default_config()
    settings_path = profile_dir / "settings.toml"
    if not settings_path.exists():
        settings_path.write_text(SETTINGS_TEMPLATE, encoding="utf-8")
    settings = profilelib.validate_settings(profilelib.read_toml(settings_path))

    result = stats.compute(messages, config)
    (profile_dir / "stats.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    pairs = extract_pairs(messages, config)
    holdout_path = profile_dir / "holdout.jsonl"
    history_path = profile_dir / "holdout-history.jsonl"
    holdout = _load_holdout(holdout_path)
    if reset_holdout and holdout:
        # Pairs already shown in a blind test must never be tested again.
        with history_path.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(h, ensure_ascii=False) + "\n" for h in holdout)
        holdout = []
    if reset_holdout or (not holdout and holdout_size > 0):
        shown = _load_holdout(history_path)
        fresh = [p for p in pairs if not near_holdout(p, shown)]
        holdout = [asdict(p) for p in select_holdout(fresh, holdout_size, seed)]
        holdout_path.write_text(
            "".join(json.dumps(h, ensure_ascii=False) + "\n" for h in holdout), encoding="utf-8"
        )

    summary: dict[str, Any] = {"holdout": len(holdout), "registers": {}}
    built: dict[str, int] = {}
    for name, reg_stats in result["registers"].items():
        reg_dir = profile_dir / "registers" / name
        reg_dir.mkdir(parents=True, exist_ok=True)
        own = [p for p in pairs if p.register == name and not near_holdout(p, holdout)]
        answered = [p for p in own if p.context]
        bank = select_bank(answered, bank_size, seed)
        initiations = select_bank([p for p in own if not p.context], MAX_INITIATIONS, seed)
        lang = reg_stats["lang"]
        block = {**reg_stats, "min_words_for_rate": config.min_words_for_rate}
        rules = profilelib.resolve(settings, block, name)
        (reg_dir / "facts.md").write_text(
            render_facts(name, reg_stats, rules, config.min_words_for_rate),
            encoding="utf-8",
        )
        (reg_dir / "examples.md").write_text(
            render_examples(name, lang, bank, initiations, strip_yo=rules.yo == "never"),
            encoding="utf-8",
        )
        pool_size = write_pool(reg_dir / "pairs.jsonl", answered, seed, rules.yo == "never")
        guide = reg_dir / "guide.md"
        if not guide.exists():
            guide.write_text(render_guide_template(name, lang), encoding="utf-8")
        built[name] = reg_stats["n_bursts"]
        summary["registers"][name] = {"pairs": len(answered), "bank": len(bank), "pool": pool_size}

    (profile_dir / "profile.md").write_text(render_index(settings, config, built), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a humanize profile from the corpus.")
    parser.add_argument("--profile", type=Path, required=True, help="profile directory")
    parser.add_argument("--corpus", type=Path, default=corpus.DEFAULT_DIR)
    parser.add_argument("--bank-size", type=int, default=40, help="pairs per register (30-50)")
    parser.add_argument("--holdout", type=int, default=20, help="pairs kept for the blind test")
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument(
        "--reset-holdout", action="store_true", help="pick a new holdout set (old one is lost)"
    )
    args = parser.parse_args(argv)
    if not args.corpus.exists():
        print(f"{args.corpus}: not found, run an ingest script first", file=sys.stderr)
        return 2
    try:
        summary = build(
            args.profile,
            corpus.read(args.corpus),
            bank_size=args.bank_size,
            holdout_size=args.holdout,
            seed=args.seed,
            reset_holdout=args.reset_holdout,
        )
    except (registers.ConfigError, profilelib.ProfileError) as exc:
        print(exc, file=sys.stderr)
        return 2
    for name, info in summary["registers"].items():
        print(f"{name}: {info['pairs']} answered turns, {info['bank']} in the bank")
    print(f"holdout: {summary['holdout']} pairs -> {args.profile / 'holdout.jsonl'}")
    print(f"profile -> {args.profile / 'profile.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
