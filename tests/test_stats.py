from pathlib import Path

import pytest

from pipeline import corpus, registers, stats
from pipeline.corpus import Message


def _msg(mid: int, owner: bool, text: str, ts: str | None, **kw: str) -> Message:
    base = {
        "platform": "telegram",
        "chat_id": "1",
        "chat_name": "Вася",
        "chat_type": "private",
    }
    base.update(kw)
    return Message(
        msg_id=str(mid),
        ts=ts,
        author_id="me" if owner else "x",
        author_name="",
        is_owner=owner,
        text=text,
        **base,  # type: ignore[arg-type]
    )


class TestBursts:
    def test_gap_boundary_and_interruption(self) -> None:
        chat = [
            _msg(1, False, "a", "2026-01-01T10:00:00+00:00"),
            _msg(2, True, "b", "2026-01-01T10:01:00+00:00"),
            _msg(3, True, "c", "2026-01-01T10:03:00+00:00"),
            _msg(4, True, "d", "2026-01-01T10:05:01+00:00"),
            _msg(5, False, "e", "2026-01-01T10:05:30+00:00"),
            _msg(6, True, "f", "2026-01-01T10:05:40+00:00"),
        ]
        bursts = corpus.owner_bursts(chat, gap_seconds=120)
        assert [[m.msg_id for m in b.messages] for b in bursts] == [["2", "3"], ["4"], ["6"]]
        assert [b.index for b in bursts] == [1, 3, 5]

    def test_no_timestamps_never_join(self) -> None:
        chat = [_msg(1, True, "a", None), _msg(2, True, "b", None)]
        assert len(corpus.owner_bursts(chat, gap_seconds=120)) == 2

    def test_empty(self) -> None:
        assert corpus.owner_bursts([], gap_seconds=120) == []


CONFIG = """
[settings]
burst_gap_seconds = 60

[exclude]
chats = ["Спам"]

[[register]]
name = "clients"
lang = "en"
[register.match]
platform = ["slack"]

[[register]]
name = "work"
[register.match]
platform = ["slack"]

[[register]]
name = "friends"
[register.match]
platform = ["telegram"]
chat_type = ["private"]

[[register]]
name = "petya"
[register.match]
chats = ["999"]
"""


class TestRegisters:
    @pytest.fixture
    def config(self, tmp_path: Path) -> registers.Config:
        path = tmp_path / "corpus.toml"
        path.write_text(CONFIG, encoding="utf-8")
        return registers.load(path)

    def test_settings(self, config: registers.Config) -> None:
        assert config.burst_gap_seconds == 60
        assert [r.name for r in config.registers] == ["clients", "work", "friends", "petya"]

    @pytest.mark.parametrize(
        ("platform", "chat_type", "chat_id", "chat_name", "lang", "expected"),
        [
            ("slack", "group", "C1", "general", "en", "clients"),
            ("slack", "group", "C1", "general", "ru", "work"),
            ("telegram", "private", "1", "Вася", "ru", "friends"),
            ("telegram", "group", "2", "Чат", "ru", None),
            ("telegram", "private", "3", "Спам", "ru", None),
            ("telegram", "group", "999", "Петя", "ru", "petya"),
            ("text", "private", "Петя", "999", "ru", "petya"),
        ],
    )
    def test_match(
        self,
        config: registers.Config,
        platform: str,
        chat_type: str,
        chat_id: str,
        chat_name: str,
        lang: str,
        expected: str | None,
    ) -> None:
        msg = _msg(
            1,
            True,
            "x",
            None,
            platform=platform,
            chat_type=chat_type,
            chat_id=chat_id,
            chat_name=chat_name,
        )
        reg = config.assign(msg, lang)
        assert (reg.name if reg else None) == expected

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "c.toml"
        path.write_text(
            '[[register]]\nname = "a"\n[register.match]\nplatfrom = ["slack"]\n', encoding="utf-8"
        )
        with pytest.raises(registers.ConfigError, match="platfrom"):
            registers.load(path)

    def test_duplicate_name_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "c.toml"
        path.write_text('[[register]]\nname = "a"\n[[register]]\nname = "a"\n', encoding="utf-8")
        with pytest.raises(registers.ConfigError, match="duplicate"):
            registers.load(path)

    def test_bad_lang_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "c.toml"
        path.write_text('[[register]]\nname = "a"\nlang = "de"\n', encoding="utf-8")
        with pytest.raises(registers.ConfigError, match="lang"):
            registers.load(path)

    def test_default_config_splits_by_language(self) -> None:
        config = registers.default_config()
        msg = _msg(1, True, "x", None)
        assert config.assign(msg, "ru").name == "ru"  # type: ignore[union-attr]
        assert config.assign(msg, "en").name == "en"  # type: ignore[union-attr]
        assert config.assign(msg, "other") is None


class TestCompute:
    def test_hand_counted_register(self) -> None:
        messages = [
            _msg(1, True, "ну, просто ок", "2026-01-01T10:00:00+00:00"),
            _msg(2, True, "Щас скину ещё.", "2026-01-01T10:00:30+00:00"),
            _msg(3, True, "понял понял))", "2026-01-01T10:30:00+00:00"),
            _msg(4, False, "А ты как, норм — да?", "2026-01-01T10:31:00+00:00"),
        ]
        result = stats.compute(messages, registers.default_config(gap=120))
        reg = result["registers"]["ru"]
        assert reg["n_messages"] == 3
        assert reg["n_bursts"] == 2
        assert reg["n_chats"] == 1
        assert reg["multi_message_burst_share"] == 0.5
        assert reg["messages_per_burst"]["median"] == 1.5
        assert reg["words_per_message"]["median"] == 3
        assert reg["words_per_burst"]["median"] == 4
        assert reg["capital_start_share"] == pytest.approx(1 / 3, abs=1e-3)
        assert reg["ending"]["period"] == pytest.approx(1 / 3, abs=1e-3)
        assert reg["ending"]["bracket"] == pytest.approx(1 / 3, abs=1e-3)
        assert reg["ending"]["none"] == pytest.approx(1 / 3, abs=1e-3)
        assert reg["yo"]["word_share"] == 0.125
        assert reg["yo"]["known_with_yo"] == 1
        assert reg["yo"]["known_total"] == 1
        assert reg["commas"]["per_100_words"] == 12.5
        assert reg["commas"]["per_100_words_per_burst"]["n"] == 1
        assert reg["commas"]["per_100_words_per_burst"]["median"] == pytest.approx(16.667, abs=1e-3)
        assert reg["dashes"]["per_100_words"] == 0
        assert reg["bracket_smiley_message_share"] == pytest.approx(1 / 3, abs=1e-3)
        assert reg["emoji"]["message_share"] == 0
        assert reg["profanity_message_share"] == 0
        fillers = {f["word"]: f["count"] for f in reg["fillers"]}
        assert fillers == {"ну": 1, "просто": 1, "щас": 1, "ок": 1}
        assert reg["en"]["negation_contraction_share"] is None

    def test_other_authors_ignored(self) -> None:
        messages = [_msg(1, False, "Привет, как дела — норм?", "2026-01-01T10:00:00+00:00")]
        assert stats.compute(messages, registers.default_config())["registers"] == {}

    def test_en_register(self) -> None:
        messages = [
            _msg(1, True, "I don't think so, it is not ready", "2026-01-01T10:00:00+00:00"),
            _msg(2, True, "We cannot ship it", "2026-01-01T12:00:00+00:00"),
        ]
        reg = stats.compute(messages, registers.default_config())["registers"]["en"]
        assert reg["en"]["apostrophe_message_share"] == 0.5
        assert reg["en"]["negation_contraction_share"] == pytest.approx(1 / 3, abs=1e-3)

    def test_burst_language_decides_register(self) -> None:
        messages = [
            _msg(1, True, "окей", "2026-01-01T10:00:00+00:00"),
            _msg(2, True, "PR", "2026-01-01T10:00:10+00:00"),
        ]
        result = stats.compute(messages, registers.default_config())
        assert result["registers"]["ru"]["n_messages"] == 2
        assert "en" not in result["registers"]


class TestChats:
    def test_listing(self) -> None:
        messages = [
            _msg(1, True, "привет", "2026-01-01T10:00:00+00:00"),
            _msg(2, False, "hi", "2026-01-02T10:00:00+00:00"),
            _msg(3, True, "ok then", "2026-01-03T10:00:00+00:00", chat_id="2", chat_name="Bob"),
        ]
        rows = stats.list_chats(messages)
        assert [(r["chat_name"], r["owner_messages"], r["messages"]) for r in rows] == [
            ("Вася", 1, 2),
            ("Bob", 1, 1),
        ]
        assert rows[0]["owner_ru_share"] == 1.0
        assert rows[0]["first"] == "2026-01-01"
        assert rows[0]["last"] == "2026-01-02"
