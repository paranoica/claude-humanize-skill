import json
import zipfile
from pathlib import Path

import pytest

from pipeline import corpus, ingest_slack, ingest_telegram, ingest_text

# 2026-01-01T10:00:00Z
T0 = 1767261600


def _tg_msg(mid: int, from_id: str, text: object, offset: int = 0, **extra: object) -> dict:
    msg = {
        "id": mid,
        "type": "message",
        "date": "2026-01-01T13:00:00",
        "date_unixtime": str(T0 + offset),
        "from": "x",
        "from_id": from_id,
        "text": text,
    }
    msg.update(extra)
    return msg


def _full_export() -> dict:
    return {
        "personal_information": {"user_id": 111, "first_name": "Owner"},
        "chats": {
            "list": [
                {
                    "name": "Вася",
                    "type": "personal_chat",
                    "id": 222,
                    "messages": [
                        _tg_msg(1, "user222", "привет, как ты?"),
                        _tg_msg(
                            2,
                            "user111",
                            ["норм ", {"type": "bold", "text": "ваще"}, " ок"],
                            60,
                            reply_to_message_id=1,
                        ),
                        {"id": 3, "type": "service", "action": "phone_call", "actor_id": "user111"},
                        _tg_msg(4, "user111", "", 70, media_type="sticker", sticker_emoji="👍"),
                        _tg_msg(5, "user111", "чужой текст", 80, forwarded_from="Канал"),
                        _tg_msg(6, "user111", "смотри", 90, photo="photos/1.jpg"),
                    ],
                },
                {
                    "name": "Работа",
                    "type": "private_supergroup",
                    "id": 333,
                    "messages": [_tg_msg(10, "user444", "всем привет")],
                },
            ]
        },
        "left_chats": {
            "list": [
                {
                    "name": "Старый",
                    "type": "private_group",
                    "id": 444,
                    "messages": [_tg_msg(20, "user111", "пока")],
                }
            ]
        },
    }


class TestTelegram:
    def test_full_export(self) -> None:
        msgs = ingest_telegram.parse(_full_export())
        vasya = [m for m in msgs if m.chat_id == "222"]
        assert [m.msg_id for m in vasya] == ["1", "2", "6"]
        reply = vasya[1]
        assert reply.text == "норм ваще ок"
        assert reply.is_owner is True
        assert reply.reply_to == "1"
        assert reply.chat_type == "private"
        assert reply.chat_name == "Вася"
        assert reply.platform == "telegram"
        assert reply.ts == "2026-01-01T10:01:00+00:00"
        assert vasya[0].is_owner is False

    def test_group_types_and_left_chats(self) -> None:
        msgs = ingest_telegram.parse(_full_export())
        types = {m.chat_id: m.chat_type for m in msgs}
        assert types == {"222": "private", "333": "group", "444": "group"}

    def test_single_chat_needs_owner(self) -> None:
        data = {
            "name": "Вася",
            "type": "personal_chat",
            "id": 222,
            "messages": [_tg_msg(1, "user111", "ок")],
        }
        with pytest.raises(corpus.OwnerNotFoundError):
            ingest_telegram.parse(data)
        msgs = ingest_telegram.parse(data, owner_id="111")
        assert msgs[0].is_owner is True
        assert ingest_telegram.parse(data, owner_id="user111")[0].is_owner is True

    def test_naive_date_without_unixtime(self) -> None:
        msg = _tg_msg(1, "user111", "ок")
        del msg["date_unixtime"]
        data = {"name": "a", "type": "personal_chat", "id": 1, "messages": [msg]}
        assert ingest_telegram.parse(data, owner_id="111")[0].ts == "2026-01-01T13:00:00+00:00"

    def test_bot_and_saved(self) -> None:
        for tg_type, expected in (("bot_chat", "bot"), ("saved_messages", "saved")):
            data = {"name": "a", "type": tg_type, "id": 1, "messages": [_tg_msg(1, "user111", "x")]}
            assert ingest_telegram.parse(data, owner_id="111")[0].chat_type == expected


def _slack_zip(path: Path, prefix: str = "") -> Path:
    files = {
        "users.json": [
            {"id": "U1", "name": "owner", "real_name": "Owner Name", "profile": {}},
            {"id": "U2", "name": "petya", "real_name": "Петя", "profile": {}},
        ],
        "channels.json": [{"id": "C1", "name": "general"}],
        "dms.json": [{"id": "D1", "members": ["U1", "U2"]}],
        "general/2026-01-01.json": [
            {
                "type": "message",
                "user": "U2",
                "text": "<@U1> глянь <https://x.com|тикет> &amp; скажи",
                "ts": f"{T0}.000100",
                "thread_ts": f"{T0}.000100",
            },
            {
                "type": "message",
                "user": "U1",
                "text": "ок, гляну",
                "ts": f"{T0 + 60}.000200",
                "thread_ts": f"{T0}.000100",
            },
            {
                "type": "message",
                "subtype": "channel_join",
                "user": "U2",
                "text": "joined",
                "ts": f"{T0 + 70}.0",
            },
            {
                "type": "message",
                "subtype": "bot_message",
                "bot_id": "B1",
                "text": "deploy ok",
                "ts": f"{T0 + 80}.0",
            },
        ],
        "D1/2026-01-02.json": [
            {"type": "message", "user": "U1", "text": "привет :wave:", "ts": f"{T0 + 86400}.000000"}
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(prefix + name, json.dumps(content, ensure_ascii=False))
    return path


class TestSlack:
    def test_zip(self, tmp_path: Path) -> None:
        msgs = ingest_slack.parse(_slack_zip(tmp_path / "e.zip"), owner="U1")
        general = [m for m in msgs if m.chat_id == "C1"]
        assert [m.text for m in general] == ["@owner глянь тикет & скажи", "ок, гляну"]
        assert general[0].reply_to is None
        assert general[1].reply_to == f"{T0}.000100"
        assert general[1].is_owner is True
        assert general[0].chat_type == "group"
        assert general[0].chat_name == "general"
        dm = [m for m in msgs if m.chat_id == "D1"]
        assert len(dm) == 1
        assert dm[0].chat_type == "private"
        assert dm[0].chat_name == "Петя"
        assert dm[0].ts == "2026-01-02T10:00:00+00:00"

    @pytest.mark.parametrize("owner", ["U1", "owner", "Owner Name"])
    def test_owner_by_id_or_name(self, tmp_path: Path, owner: str) -> None:
        msgs = ingest_slack.parse(_slack_zip(tmp_path / "e.zip"), owner=owner)
        assert {m.author_id for m in msgs if m.is_owner} == {"U1"}

    def test_unknown_owner(self, tmp_path: Path) -> None:
        with pytest.raises(corpus.OwnerNotFoundError):
            ingest_slack.parse(_slack_zip(tmp_path / "e.zip"), owner="nobody")

    def test_prefixed_zip(self, tmp_path: Path) -> None:
        msgs = ingest_slack.parse(_slack_zip(tmp_path / "e.zip", prefix="Export Jan/"), owner="U1")
        assert len(msgs) == 3


class TestText:
    def test_jsonl(self, tmp_path: Path) -> None:
        src = tmp_path / "notes.jsonl"
        src.write_text(
            '{"text": "привет", "is_owner": true, "ts": "2026-01-01T10:00:00Z"}\n'
            "\n"
            '{"text": "ок", "author": "Вася"}\n',
            encoding="utf-8",
        )
        msgs = ingest_text.parse(src)
        assert [m.text for m in msgs] == ["привет", "ок"]
        assert msgs[0].ts == "2026-01-01T10:00:00+00:00"
        assert msgs[0].is_owner is True
        assert msgs[1].is_owner is False
        assert msgs[1].ts is None
        assert msgs[0].chat_id == "notes"
        assert msgs[0].platform == "text"

    def test_jsonl_owner_name(self, tmp_path: Path) -> None:
        src = tmp_path / "c.jsonl"
        src.write_text(
            '{"text": "a", "author": "Я"}\n{"text": "b", "author": "Вася"}\n', encoding="utf-8"
        )
        assert [m.is_owner for m in ingest_text.parse(src, owner_name="Я")] == [True, False]

    def test_jsonl_missing_text(self, tmp_path: Path) -> None:
        src = tmp_path / "bad.jsonl"
        src.write_text('{"text": "a"}\n{"author": "x"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="line 2"):
            ingest_text.parse(src)

    def test_txt_paragraphs(self, tmp_path: Path) -> None:
        src = tmp_path / "post.txt"
        src.write_text("первый абзац\nвторая строка\n\n\nвторой абзац\n", encoding="utf-8")
        msgs = ingest_text.parse(src)
        assert [m.text for m in msgs] == ["первый абзац\nвторая строка", "второй абзац"]
        assert all(m.is_owner for m in msgs)
        assert msgs[0].chat_type == "text"


class TestCorpusIO:
    def test_roundtrip_and_dedupe(self, tmp_path: Path) -> None:
        msgs = ingest_telegram.parse(_full_export())
        corpus.write(msgs, tmp_path / "a.jsonl")
        corpus.write(msgs[:2], tmp_path / "b.jsonl")
        loaded = corpus.read(tmp_path)
        assert len(loaded) == len(msgs)
        assert loaded[0] == msgs[0]


def test_text_ids_unique_across_files(tmp_path: Path) -> None:
    for sub, text in (("a", "раз"), ("b", "два")):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "x.jsonl").write_text(
            json.dumps({"text": text, "is_owner": True, "chat": "Вася"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    msgs = ingest_text.parse(tmp_path / "a" / "x.jsonl") + ingest_text.parse(
        tmp_path / "b" / "x.jsonl"
    )
    corpus.write(msgs, tmp_path / "out" / "t.jsonl")
    assert [m.text for m in corpus.read(tmp_path / "out")] == ["раз", "два"]


def test_text_explicit_id_kept(tmp_path: Path) -> None:
    src = tmp_path / "c.jsonl"
    src.write_text('{"text": "a", "id": 7}\n', encoding="utf-8")
    assert ingest_text.parse(src)[0].msg_id == "7"


def test_slack_subteam_mention() -> None:
    assert ingest_slack.render("<!subteam^S1|@devs> глянь", {}) == "@devs глянь"
    assert ingest_slack.render("<!here> ок", {}) == "@here ок"


def test_telegram_cli_bad_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "result.json"
    src.write_text("not json", encoding="utf-8")
    assert ingest_telegram.main([str(src), "-o", str(tmp_path / "o.jsonl")]) == 2
    assert "result.json" in capsys.readouterr().err
