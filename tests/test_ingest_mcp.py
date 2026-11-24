import json
from pathlib import Path

import pytest

from pipeline import corpus, ingest_mcp

RECORDS = [
    {"id": 5, "sender": "я", "date": "2026-11-17T14:19:32+00:00", "sender_id": 1, "out": True,
     "text": "ок, ща", "reply_to": 4},
    {"id": 4, "sender": "Эрик", "date": "2026-11-17T14:19:05+00:00", "sender_id": 2,
     "text": "Всё ещё ждём?"},
    {"id": 3, "sender": "Эрик", "date": "2026-11-17T14:18:00+00:00", "sender_id": 2,
     "action": "pin", "text": "pinned"},
    {"id": 2, "sender": "я", "date": "2026-11-17T14:17:00+00:00", "sender_id": 1, "out": True,
     "text": "чужое", "forwarded": {"from_user": "x"}},
    {"id": 1, "sender": "я", "date": "2026-11-17T14:16:00+00:00", "sender_id": 1, "out": True,
     "text": None, "media": "", "duration": 3},
]  # fmt: skip


def _saved(tmp_path: Path, records: list[dict], name: str = "r.txt") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"result": json.dumps({"results": records})}), encoding="utf-8")
    return path


def test_parse_keeps_text_exactly(tmp_path: Path) -> None:
    results = ingest_mcp.load_results(_saved(tmp_path, RECORDS))
    msgs = ingest_mcp.parse_telegram(results, "2", "Эрик", "User")
    assert [(m.msg_id, m.text, m.is_owner) for m in msgs] == [
        ("4", "Всё ещё ждём?", False),
        ("5", "ок, ща", True),
    ]
    assert msgs[1].reply_to == "4"
    assert msgs[0].chat_type == "private"
    assert msgs[0].ts == "2026-11-17T14:19:05+00:00"


@pytest.mark.parametrize(
    ("mcp_type", "expected"),
    [
        ("User", "private"),
        ("Group (Basic)", "group"),
        ("Supergroup", "group"),
        ("Channel", "channel"),
    ],
)
def test_chat_types(mcp_type: str, expected: str) -> None:
    assert ingest_mcp.chat_type(mcp_type) == expected


def test_self_chat_is_saved() -> None:
    msgs = ingest_mcp.parse_telegram(RECORDS[:1], "1", "я", "User")
    assert msgs[0].chat_type == "saved"


@pytest.mark.parametrize(
    "content", ["not json", '{"other": 1}', '{"result": "[]"}', '{"result": "{\\"results\\": 5}"}']
)
def test_bad_files(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.txt"
    path.write_text(content)
    with pytest.raises(ValueError):
        ingest_mcp.load_results(path)


def test_cli_manifest(tmp_path: Path) -> None:
    manifest = [
        {"chat_id": 2, "name": "Эрик", "type": "User", "file": str(_saved(tmp_path, RECORDS))},
        {"chat_id": 3, "name": "маленький", "type": "User", "file": None},
        {"chat_id": 4, "name": "битый", "type": "User", "file": str(tmp_path / "missing.txt")},
    ]
    (tmp_path / "m.json").write_text(json.dumps(manifest, ensure_ascii=False))
    out = tmp_path / "c" / "t.jsonl"
    assert (
        ingest_mcp.main(["telegram", "--manifest", str(tmp_path / "m.json"), "-o", str(out)]) == 0
    )
    assert len(corpus.read(out)) == 2


SLACK_PAGE = """Channel: DM (D1)

=== Message from Anna B <a@x.com> (U1) at 2026-11-23 18:17:04 +03 === 
Message TS: 1795447024.043229
обращайся)
Reactions: raised_hands (1)

=== Message from Kate M <k@x.com> (U2) at 2026-11-23 18:16:49 +03 === 
Message TS: 1795447009.858179
спасибо, большое!!

а это вторая часть
Files: image.png (ID: F1, image/png, 1 KB)

=== Message from Anna B <a@x.com> (U1) at 2026-11-23 18:13:16 +03 === 
Message TS: 1795446796.546579

Files: a.pdf (ID: F2, application/pdf, 9 KB)
"""


class TestSlack:
    def test_parse_text(self) -> None:
        records = ingest_mcp.parse_slack_text(SLACK_PAGE)
        assert [(r["uid"], r["ts"], r["text"]) for r in records] == [
            ("U1", "1795447024.043229", "обращайся)"),
            ("U2", "1795447009.858179", "спасибо, большое!!\n\nа это вторая часть"),
            ("U1", "1795446796.546579", ""),
        ]

    def test_messages(self) -> None:
        records = ingest_mcp.parse_slack_text(SLACK_PAGE)
        msgs = ingest_mcp.slack_to_messages(
            records, "D1", "DM with Kate", "dm", "U1", "1795447009.858179"
        )
        assert [m.is_owner for m in msgs] == [True, False]
        assert msgs[0].chat_type == "private"
        assert msgs[0].reply_to == "1795447009.858179"
        assert msgs[1].reply_to is None
        assert msgs[0].ts.startswith("2026-11-23T15:17:04")

    def test_saved_result_and_manifest(self, tmp_path: Path) -> None:
        saved = tmp_path / "saved.txt"
        saved.write_text(json.dumps({"result": json.dumps({"messages": SLACK_PAGE})}))
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "page-01.txt").write_text(SLACK_PAGE)
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps([{
            "channel_id": "D1", "name": "DM", "kind": "dm", "pages": ["raw/page-01.txt"],
            "threads": [{"parent_ts": "1795447009.858179", "file": None, "saved_path": str(saved)}],
        }]))  # fmt: skip
        out = tmp_path / "s.jsonl"
        assert (
            ingest_mcp.main(["slack", "--manifest", str(manifest), "--owner", "U1", "-o", str(out)])
            == 0
        )
        assert len(corpus.read(out)) == 2


def test_slack_connect_header() -> None:
    text = (
        "=== Message from Sam Norman (U0ABC12DEF, team: Acme) at 2026-11-20 10:00:00 +03 === \n"
        "Message TS: 1795000000.000100\nhello\n\n"
        "=== Message from Bill S <b@acme.com> (U0AB1CD2E, team: Acme’s Org) "
        "at 2026-11-20 10:01:00 +03 === \n"
        "Message TS: 1795000060.000100\nhi there\n"
    )
    records = ingest_mcp.parse_slack_text(text)
    assert [(r["uid"], r["text"]) for r in records] == [
        ("U0ABC12DEF", "hello"),
        ("U0AB1CD2E", "hi there"),
    ]


def test_slack_markup_rendered() -> None:
    text = "<@U0123ABCD|Anna Becker> see <https://x.com|the doc> &amp; <#C1|general>"
    records = [{"uid": "U2", "name": "x", "ts": "1795000000.000100", "text": text}]
    msg = ingest_mcp.slack_to_messages(records, "C1", "c", "channel", "U1")[0]
    assert msg.text == "@Anna Becker see the doc & #general"


def test_slack_user_line_starting_with_files_kept() -> None:
    text = (
        "=== Message from A (U1) at 2026-11-20 10:00:00 +03 === \n"
        "Message TS: 1795000000.000100\nHere is the plan\nFiles: see attached, more later\n"
        "Reactions: tada (2), eyes (1)\n"
    )
    assert (
        ingest_mcp.parse_slack_text(text)[0]["text"]
        == "Here is the plan\nFiles: see attached, more later"
    )


def test_slack_reactions_with_skin_tone_stripped() -> None:
    text = (
        "=== Message from A (U1) at 2026-11-20 10:00:00 +03 === \n"
        "Message TS: 1795000000.000100\nок\nReactions: raised_hands::skin-tone-2 (1), eyes (2)\n"
    )
    assert ingest_mcp.parse_slack_text(text)[0]["text"] == "ок"
