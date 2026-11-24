"""Registers: which chats a style applies to, read from a profile's ``corpus.toml``.

Example::

    [settings]
    burst_gap_seconds = 180   # pause that still continues one thought
    min_words_for_rate = 5    # shorter turns are left out of per-turn comma rates

    [exclude]
    chats = ["Mom", "123456"] # never read, by chat name or id

    [[register]]
    name = "clients"
    lang = "en"               # optional; also filters by the language of each turn
    description = "Client threads in Slack"
    [register.match]
    platform = ["slack"]
    chat_type = ["group"]
    chats = ["acme-dev"]      # chat name or id

A turn goes to the first register whose every given condition matches. Turns that
match nothing stay out of the profile.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.corpus import Message

DEFAULT_GAP_SECONDS = 180
DEFAULT_MIN_WORDS = 5
LANGS = ("ru", "en")

_KEYS: dict[str, set[str]] = {
    "": {"settings", "exclude", "register"},
    "settings": {"burst_gap_seconds", "min_words_for_rate"},
    "exclude": {"chats"},
    "register": {"name", "lang", "description", "match"},
    "match": {"platform", "chat_type", "chats"},
}


class ConfigError(ValueError):
    """``corpus.toml`` is malformed."""


@dataclass(frozen=True)
class Register:
    """One way of writing, tied to a group of chats."""

    name: str
    lang: str | None = None
    description: str = ""
    platforms: frozenset[str] = frozenset()
    chat_types: frozenset[str] = frozenset()
    chats: frozenset[str] = frozenset()

    def matches(self, msg: Message, lang: str) -> bool:
        """True if the message's chat and the turn's language fit every condition."""
        if self.lang and lang != self.lang:
            return False
        if self.platforms and msg.platform not in self.platforms:
            return False
        if self.chat_types and msg.chat_type not in self.chat_types:
            return False
        return not self.chats or bool({msg.chat_id, msg.chat_name} & self.chats)


@dataclass(frozen=True)
class Config:
    """Corpus selection and register rules."""

    registers: tuple[Register, ...]
    exclude_chats: frozenset[str] = frozenset()
    burst_gap_seconds: float = DEFAULT_GAP_SECONDS
    min_words_for_rate: int = DEFAULT_MIN_WORDS
    names: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "names", tuple(r.name for r in self.registers))

    def excluded(self, msg: Message) -> bool:
        return bool({msg.chat_id, msg.chat_name} & self.exclude_chats)

    def assign(self, msg: Message, lang: str) -> Register | None:
        """Picks the register for a turn that starts with ``msg`` and is written in ``lang``."""
        if self.excluded(msg):
            return None
        return next((r for r in self.registers if r.matches(msg, lang)), None)


def default_config(gap: float = DEFAULT_GAP_SECONDS) -> Config:
    """Used without a ``corpus.toml``: one register per language, every chat included."""
    return Config(
        registers=tuple(Register(name=lang, lang=lang) for lang in LANGS),
        burst_gap_seconds=gap,
    )


def _check_keys(section: str, table: Any) -> None:
    if not isinstance(table, dict):
        raise ConfigError(f"[{section}] must be a table")
    unknown = set(table) - _KEYS[section]
    if unknown:
        raise ConfigError(
            f"unknown key(s) in [{section or 'top level'}]: {', '.join(sorted(unknown))}"
        )


def _strings(value: Any, where: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(v, str | int) for v in value):
        raise ConfigError(f"{where} must be a list of strings")
    return frozenset(str(v) for v in value)


def parse(data: dict[str, Any]) -> Config:
    """Validates a decoded ``corpus.toml``.

    Raises:
        ConfigError: Unknown keys, a register without a unique name, or a bad value.
    """
    _check_keys("", data)
    settings = data.get("settings", {})
    _check_keys("settings", settings)
    exclude = data.get("exclude", {})
    _check_keys("exclude", exclude)

    regs: list[Register] = []
    for raw in data.get("register", []):
        _check_keys("register", raw)
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError("every [[register]] needs a name")
        if name in {r.name for r in regs}:
            raise ConfigError(f"duplicate register name {name!r}")
        lang = raw.get("lang")
        if lang is not None and lang not in LANGS:
            raise ConfigError(f"register {name!r}: lang must be one of {LANGS}, got {lang!r}")
        match = raw.get("match", {})
        _check_keys("match", match)
        regs.append(
            Register(
                name=name,
                lang=lang,
                description=str(raw.get("description", "")),
                platforms=_strings(match.get("platform", []), f"{name}.match.platform"),
                chat_types=_strings(match.get("chat_type", []), f"{name}.match.chat_type"),
                chats=_strings(match.get("chats", []), f"{name}.match.chats"),
            )
        )
    if not regs:
        raise ConfigError("no [[register]] defined")

    gap = settings.get("burst_gap_seconds", DEFAULT_GAP_SECONDS)
    min_words = settings.get("min_words_for_rate", DEFAULT_MIN_WORDS)
    if not isinstance(gap, int | float) or gap < 0:
        raise ConfigError("settings.burst_gap_seconds must be a non-negative number")
    if not isinstance(min_words, int) or min_words < 1:
        raise ConfigError("settings.min_words_for_rate must be a positive integer")
    return Config(
        registers=tuple(regs),
        exclude_chats=_strings(exclude.get("chats", []), "exclude.chats"),
        burst_gap_seconds=gap,
        min_words_for_rate=min_words,
    )


def load(path: Path) -> Config:
    """Reads and validates a ``corpus.toml``."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return parse(data)
