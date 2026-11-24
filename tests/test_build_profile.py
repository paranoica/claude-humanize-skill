import json
import zlib
from pathlib import Path

import pytest

from pipeline import build_profile as bp
from pipeline import registers
from pipeline.corpus import Message

T0 = 1767261600


def _ts(offset: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(T0 + offset, tz=UTC).isoformat()


def _m(
    mid: int, author: str, text: str, offset: int, chat: str = "1", chat_type: str = "private"
) -> Message:
    return Message(
        platform="telegram",
        chat_id=chat,
        chat_name=f"chat{chat}",
        chat_type=chat_type,
        msg_id=str(mid),
        ts=_ts(offset),
        author_id=author,
        author_name=author,
        is_owner=author == "me",
        text=text,
    )


def _word(i: int) -> str:
    letters = "абвгдежзиклмнопрстуфхцчшэюя"
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, len(letters))
        out = letters[r] + out
    return out


def _pair(
    key: str,
    n_words: int = 2,
    chat: str = "1",
    ts_offset: int = 0,
    reply: str | None = None,
    ctx: bool = True,
) -> bp.Pair:
    return bp.Pair(
        key=key,
        register="r",
        lang="ru",
        platform="telegram",
        chat_id=chat,
        chat_type="private",
        ts=_ts(ts_offset),
        context=[("", "ну что там завтра?")] if ctx else [],
        reply=[reply or f"ответ {_word(zlib.crc32(key.encode()) % 10**6)}"],
        n_words=n_words,
    )


class TestMask:
    def test_ru_labels(self) -> None:
        text = "глянь https://x.com/a, пиши a.b@mail.ru или +7 (999) 123-45-67, код 1234567890"
        assert bp.mask(text, "ru") == "глянь <ссылка>, пиши <почта> или <телефон>, код <число>"

    def test_en_labels(self) -> None:
        assert bp.mask("see www.example.com", "en") == "see <link>"

    def test_dates_and_ids(self) -> None:
        assert bp.mask("от 2026-01-01 заказ 123456789012", "ru") == "от 2026-01-01 заказ <число>"
        assert bp.mask("звони 8 999 123 45 67", "ru") == "звони <телефон>"

    def test_short_numbers_kept(self) -> None:
        assert (
            bp.mask("в 10:30 два релиза, 2 штуки, 2026", "ru")
            == "в 10:30 два релиза, 2 штуки, 2026"
        )


class TestExtractPairs:
    def test_private_context(self) -> None:
        chat = [
            _m(1, "me", "ранее", 0),
            _m(2, "x", "привет", 100),
            _m(3, "x", "ты тут?", 110),
            _m(4, "me", "ща", 120),
            _m(5, "me", "гляну", 130),
        ]
        pairs = bp.extract_pairs(chat, registers.default_config())
        assert [p.reply for p in pairs] == [["ранее"], ["ща", "гляну"]]
        assert pairs[0].context == []
        assert pairs[1].context == [("", "привет"), ("", "ты тут?")]
        assert pairs[1].key == "telegram:1:4"

    def test_group_speakers_and_limit(self) -> None:
        chat = [
            _m(i, a, f"сообщение {i}", i * 10, chat_type="group") for i, a in enumerate("abcab", 1)
        ]
        chat.append(_m(9, "me", "ок понял", 100, chat_type="group"))
        pair = bp.extract_pairs(chat, registers.default_config())[0]
        assert len(pair.context) == bp.MAX_CONTEXT
        assert [s for s, _ in pair.context] == ["A", "B", "C", "A"]

    def test_context_window(self) -> None:
        chat = [_m(1, "x", "старое", 0), _m(2, "me", "ок", bp.CONTEXT_WINDOW_S + 1)]
        assert bp.extract_pairs(chat, registers.default_config())[0].context == []

    def test_wordless_and_long_replies_dropped(self) -> None:
        long_reply = " ".join(["слово"] * (bp.MAX_REPLY_WORDS + 1))
        chat = [
            _m(1, "x", "что?", 0),
            _m(2, "me", "https://x.com", 10),
            _m(3, "x", "а?", 1000),
            _m(4, "me", long_reply, 1010),
            _m(5, "x", "ну?", 2000),
            _m(6, "me", " ".join(["слово"] * bp.MAX_REPLY_WORDS), 2010),
        ]
        pairs = bp.extract_pairs(chat, registers.default_config())
        assert [p.key for p in pairs] == ["telegram:1:6"]


class TestAllocate:
    def test_proportional(self) -> None:
        assert bp._allocate({"a": 90, "b": 10}, 20) == {"a": 18, "b": 2}

    def test_capped_by_pool(self) -> None:
        assert bp._allocate({"a": 3, "b": 1}, 10) == {"a": 3, "b": 1}

    def test_remainders(self) -> None:
        alloc = bp._allocate({"a": 1, "b": 1, "c": 1}, 2)
        assert sum(alloc.values()) == 2
        assert all(v <= 1 for v in alloc.values())

    def test_empty(self) -> None:
        assert bp._allocate({}, 5) == {}
        assert bp._allocate({"a": 0}, 5) == {"a": 0}


class TestSelection:
    def test_holdout_only_answered(self) -> None:
        pairs = [_pair(f"k{i}", ctx=i % 2 == 0) for i in range(10)]
        chosen = bp.select_holdout(pairs, 3, seed=1)
        assert len(chosen) == 3
        assert all(p.context for p in chosen)

    def test_bank_dedupes_and_caps(self) -> None:
        pairs = [_pair(f"k{i}", reply="ок") for i in range(5)]
        pairs += [_pair(f"c{i}", chat="big", reply=f"разный ответ {_word(i)}") for i in range(30)]
        pairs += [_pair(f"o{i}", chat=f"o{i}", reply=f"другой ответ {_word(i)}") for i in range(30)]
        bank = bp.select_bank(pairs, 40, seed=1)
        assert len(bank) == 40
        assert sum(p.reply == ["ок"] for p in bank) == 1
        assert sum(p.chat_id == "big" for p in bank) <= 10

    def test_bank_keeps_proportions(self) -> None:
        pairs = [_pair(f"s{i}", n_words=2, chat=f"s{i}") for i in range(90)]
        pairs += [_pair(f"l{i}", n_words=30, chat=f"l{i}") for i in range(10)]
        bank = bp.select_bank(pairs, 20, seed=1)
        assert sum(p.n_words == 30 for p in bank) == 2

    def test_bank_smaller_than_n(self) -> None:
        assert len(bp.select_bank([_pair("a"), _pair("b")], 40, seed=1)) == 2

    def test_bank_deterministic(self) -> None:
        pairs = [_pair(f"k{i}", chat=f"c{i}") for i in range(100)]
        assert bp.select_bank(pairs, 30, seed=5) == bp.select_bank(pairs, 30, seed=5)


HOLD = [{"key": "h", "platform": "telegram", "chat_id": "1", "ts": _ts(0)}]


class TestNearHoldout:
    @pytest.mark.parametrize(
        ("pair", "expected"),
        [
            (_pair("h", ts_offset=99999), True),
            (_pair("x", ts_offset=bp.HOLDOUT_NEIGHBOUR_S), True),
            (_pair("x", ts_offset=-bp.HOLDOUT_NEIGHBOUR_S), True),
            (_pair("x", ts_offset=bp.HOLDOUT_NEIGHBOUR_S + 1), False),
            (_pair("x", chat="2", ts_offset=10), False),
        ],
    )
    def test_cases(self, pair: bp.Pair, expected: bool) -> None:
        assert bp.near_holdout(pair, HOLD) is expected


def _corpus(n_chats: int = 6, turns: int = 15) -> list[Message]:
    msgs = []
    mid = 0
    for c in range(n_chats):
        offset = c * 10**6
        for t in range(turns):
            offset += 4 * 3600
            mid += 1
            msgs.append(_m(mid, "x", f"а ты завтра {_word(c * 100 + t)}?", offset, chat=str(c)))
            mid += 1
            msgs.append(_m(mid, "me", f"ну ок {_word(c * 100 + t)}", offset + 30, chat=str(c)))
    return msgs


class TestBuild:
    def test_end_to_end(self, tmp_path: Path) -> None:
        summary = bp.build(tmp_path, _corpus(), bank_size=30, holdout_size=5, seed=3)
        assert summary["holdout"] == 5
        assert summary["registers"]["ru"]["bank"] == 30
        for name in ("stats.json", "settings.toml", "profile.md", "holdout.jsonl"):
            assert (tmp_path / name).exists(), name
        reg = tmp_path / "registers" / "ru"
        examples = (reg / "examples.md").read_text(encoding="utf-8")
        holdout = [json.loads(x) for x in (tmp_path / "holdout.jsonl").read_text().splitlines()]
        for h in holdout:
            assert h["reply"][0] not in examples
        facts = (reg / "facts.md").read_text(encoding="utf-8")
        assert "ё" not in facts
        assert "Буква йо (е с точками): нельзя" in facts
        assert "`registers/ru/examples.md`" in (tmp_path / "profile.md").read_text()

    def test_rebuild_keeps_holdout_and_guide(self, tmp_path: Path) -> None:
        bp.build(tmp_path, _corpus(), holdout_size=5, seed=3)
        holdout = (tmp_path / "holdout.jsonl").read_text()
        guide = tmp_path / "registers" / "ru" / "guide.md"
        guide.write_text("мой гайд", encoding="utf-8")
        bp.build(tmp_path, _corpus(), holdout_size=5, seed=99)
        assert (tmp_path / "holdout.jsonl").read_text() == holdout
        assert guide.read_text(encoding="utf-8") == "мой гайд"

    def test_reset_holdout(self, tmp_path: Path) -> None:
        bp.build(tmp_path, _corpus(), holdout_size=5, seed=3)
        first = (tmp_path / "holdout.jsonl").read_text()
        bp.build(tmp_path, _corpus(), holdout_size=5, seed=99, reset_holdout=True)
        assert (tmp_path / "holdout.jsonl").read_text() != first

    def test_bad_settings(self, tmp_path: Path) -> None:
        (tmp_path / "settings.toml").write_text('[rules]\nyo = "maybe"\n', encoding="utf-8")
        with pytest.raises(bp.profilelib.ProfileError):
            bp.build(tmp_path, _corpus())


def test_examples_strip_yo_from_context() -> None:
    pair = _pair("k")
    pair.context = [("", "ещё не всё?")]
    pair.reply = ["щас"]
    assert "ещё" in bp.render_examples("r", "ru", [pair], [])
    stripped = bp.render_examples("r", "ru", [pair], [], strip_yo=True)
    assert "ё" not in stripped
    assert "еще не все?" in stripped


def test_build_examples_without_yo(tmp_path: Path) -> None:
    msgs = _corpus()
    msgs = [m if m.is_owner else Message(**{**m.__dict__, "text": m.text + " ещё"}) for m in msgs]
    bp.build(tmp_path, msgs, holdout_size=0)
    assert "ё" not in (tmp_path / "registers" / "ru" / "examples.md").read_text(encoding="utf-8")


def test_bank_from_single_chat_fills_up() -> None:
    pairs = [_pair(f"k{i}", reply=f"ответ {_word(i)}") for i in range(50)]
    assert len(bp.select_bank(pairs, 30, seed=1)) == 30


def test_bank_prefers_spread_over_chats() -> None:
    pairs = [_pair(f"b{i}", chat="big", reply=f"большой {_word(i)}") for i in range(50)]
    pairs += [_pair(f"s{i}", chat=f"s{i}", reply=f"малый {_word(i)}") for i in range(20)]
    bank = bp.select_bank(pairs, 30, seed=1)
    assert len(bank) == 30
    assert sum(p.chat_id != "big" for p in bank) == 20


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, "1 слово"),
        (2, "2 слова"),
        (4, "4 слова"),
        (5, "5 слов"),
        (11, "11 слов"),
        (12, "12 слов"),
        (21, "21 слово"),
        (22, "22 слова"),
        (2.5, "2.5 слова"),
        (None, "n/a"),
    ],
)
def test_slov(value: float | None, expected: str) -> None:
    assert bp._slov(value) == expected


class TestReviewFixes:
    def test_repeated_exchange_excluded_by_content(self) -> None:
        hold = [
            {
                "key": "h",
                "platform": "telegram",
                "chat_id": "1",
                "ts": _ts(0),
                "reply": ["почти готово", "осталось ретраи"],
            }
        ]
        same = _pair("other", ts_offset=10**6, reply="Почти готово!")
        same.reply = ["Почти готово!", "осталось ретраи"]
        assert bp.near_holdout(same, hold) is True

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("глянь t.me/joinchat/AAAA", "глянь <ссылка>"),
            ("github.com/ivan/secret-proj тут", "<ссылка> тут"),
            ("(см. https://x.com)", "(см. <ссылка>)"),
            ("мой тел 123-45-67", "мой тел <телефон>"),
            ("в 2024-01-15 10:30 созвон", "в 2024-01-15 10:30 созвон"),
            ("версия 2.15 и файл config.py", "версия 2.15 и файл config.py"),
        ],
    )
    def test_mask_more(self, text: str, expected: str) -> None:
        assert bp.mask(text, "ru") == expected

    def test_reset_holdout_to_zero_clears_file(self, tmp_path: Path) -> None:
        bp.build(tmp_path, _corpus(), holdout_size=5, seed=3)
        bp.build(tmp_path, _corpus(), holdout_size=0, reset_holdout=True)
        assert (tmp_path / "holdout.jsonl").read_text() == ""

    def test_index_lint_command_points_at_skill_dir(self, tmp_path: Path) -> None:
        bp.build(tmp_path, _corpus(), holdout_size=0)
        assert "<skill-dir>/scripts/lint.py --register ru" in (tmp_path / "profile.md").read_text()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ключ sk-ant-api03-AbCdEf1234567890xyz", "ключ <секрет>"),
        ("пароль Qwerty123!", "пароль <секрет>"),
        ("токен ghp_a1B2c3D4e5F6g7H8i9J0", "токен <секрет>"),
        ("код 482913", "код <число>"),
        ("creds: user_good:pass_good", "creds: <секрет>"),
        ("пароль: hunter2 ок", "<секрет> ок"),
        ("Password=abc", "<секрет>"),
        ("забыл пароль от почты", "забыл пароль от почты"),
        ("в 2026 году 3 релиза", "в 2026 году 3 релиза"),
        ("glhf GPT4 и Python3", "glhf GPT4 и Python3"),
        ("обновился на Windows11 и iPhone15Pro", "обновился на Windows11 и iPhone15Pro"),
        ("тест test_build_profile_v2 упал", "тест test_build_profile_v2 упал"),
        ("years 2019 2020 2021", "years 2019 2020 2021"),
        ("бюджет 250 000 000 рублей", "бюджет 250 000 000 рублей"),
        ("стоит 15000 руб", "стоит 15000 руб"),
        ("звони (495) 123-45-67", "звони <телефон>"),
    ],
)
def test_mask_secrets(text: str, expected: str) -> None:
    assert bp.mask(text, "ru") == expected


def test_holdout_covers_small_registers() -> None:
    pairs = [_pair(f"big{i}") for i in range(200)]
    small = [_pair(f"small{i}") for i in range(5)]
    for p in small:
        p.register = "small"
    chosen = bp.select_holdout(pairs + small, 20, seed=1)
    assert len(chosen) == 20
    assert sum(p.register == "small" for p in chosen) == bp.MIN_HOLDOUT_PER_REGISTER


def test_holdout_minimum_capped_by_size() -> None:
    pairs = [_pair(f"big{i}") for i in range(50)]
    one = _pair("only")
    one.register = "tiny"
    chosen = bp.select_holdout([*pairs, one], 10, seed=1)
    assert len(chosen) == 10
    assert sum(p.register == "tiny" for p in chosen) == 1


def test_holdout_skips_masked_replies() -> None:
    pairs = [_pair(f"k{i}") for i in range(30)]
    for p in pairs[:25]:
        p.reply = ["глянь <ссылка>"]
    chosen = bp.select_holdout(pairs, 10, seed=1)
    assert len(chosen) == 5
    assert all("<" not in r for p in chosen for r in p.reply)


def test_holdout_needs_some_context() -> None:
    pairs = [_pair(f"k{i}") for i in range(10)]
    for p in pairs[:6]:
        p.context = [("A", "Полины")]
    for p in pairs[6:]:
        p.context = [("", "ты придешь завтра")]
    chosen = bp.select_holdout(pairs, 10, seed=1)
    assert sorted(p.key for p in chosen) == sorted(p.key for p in pairs[6:])


def test_reset_holdout_never_reuses_shown_pairs(tmp_path: Path) -> None:
    bp.build(tmp_path, _corpus(), holdout_size=5, seed=3)
    first = {json.loads(x)["key"] for x in (tmp_path / "holdout.jsonl").read_text().splitlines()}
    bp.build(tmp_path, _corpus(), holdout_size=5, seed=3, reset_holdout=True)
    second = {json.loads(x)["key"] for x in (tmp_path / "holdout.jsonl").read_text().splitlines()}
    assert len(second) == 5
    assert not first & second
    history = (tmp_path / "holdout-history.jsonl").read_text().splitlines()
    assert {json.loads(x)["key"] for x in history} == first
