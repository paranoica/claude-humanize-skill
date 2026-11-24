import json
from pathlib import Path

import pytest

from pipeline import build_profile
from skill.scripts import similar
from tests.test_build_profile import _corpus

POOL = [
    {"in": ["го в кино завтра?"], "out": ["го", "на что"]},
    {"in": ["скинь конспект по матану"], "out": ["ща гляну"]},
    {"in": ["как дела вообще"], "out": ["норм"]},
    {"in": ["завтра пары есть?"], "out": ["неа"]},
]


class TestRank:
    def test_best_overlap_first(self) -> None:
        top = similar.rank("есть конспект по матану?", POOL, k=1)
        assert top[0]["out"] == ["ща гляну"]

    def test_rare_word_beats_common(self) -> None:
        top = similar.rank("в кино пойдем завтра", POOL, k=2)
        assert top[0]["out"] == ["го", "на что"]

    def test_fill_without_overlap(self) -> None:
        top = similar.rank("абракадабра", POOL, k=3)
        assert len(top) == 3

    def test_k_larger_than_pool(self) -> None:
        assert len(similar.rank("кино", POOL, k=10)) == len(POOL)

    def test_empty_pool(self) -> None:
        assert similar.rank("кино", [], k=5) == []

    def test_stems_match_word_forms(self) -> None:
        assert similar.tokens("конспекты") & similar.tokens("конспект")


def test_render_ru() -> None:
    text = similar.render(POOL[:1], "ru")
    assert text.startswith("## 1\nВходящее:\n  го в кино завтра?\nОтвет:\n  го\n  на что")


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg = tmp_path / "registers" / "friends"
    reg.mkdir(parents=True)
    (reg / "pairs.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in POOL))
    src = tmp_path / "in.txt"
    src.write_text("конспект по матану есть?")
    assert (
        similar.main(["--register", "friends", "--profile", str(tmp_path), "--k", "1", str(src)])
        == 0
    )
    assert "ща гляну" in capsys.readouterr().out
    assert similar.main(["--register", "nope", "--profile", str(tmp_path), str(src)]) == 2


def test_build_writes_pool_without_holdout(tmp_path: Path) -> None:
    summary = build_profile.build(tmp_path, _corpus(), holdout_size=5, seed=3)
    pool_path = tmp_path / "registers" / "ru" / "pairs.jsonl"
    pool = [json.loads(line) for line in pool_path.read_text().splitlines()]
    assert len(pool) == summary["registers"]["ru"]["pool"] > 30
    held = [json.loads(line) for line in (tmp_path / "holdout.jsonl").read_text().splitlines()]
    replies = {tuple(p["out"]) for p in pool}
    assert all(tuple(h["reply"]) not in replies for h in held)
    assert "similar.py --register ru" in (tmp_path / "profile.md").read_text()


def test_typical_length() -> None:
    pairs = [
        {"in": ["x"], "out": ["го"]},
        {"in": ["x"], "out": ["го", "на что"]},
        {"in": ["x"], "out": ["норм"]},
    ]
    assert similar.typical(pairs) == (1, 1)
    assert similar.typical([]) == (0, 0)


def test_render_has_typical_footer() -> None:
    text = similar.render(POOL[:2], "ru")
    assert text.rstrip().endswith(
        "Обычно на похожее ты отвечаешь так: 2 слова, 2 сообщения (медиана ответов выше)."
    )
    assert "Typical reply to messages like this: 2 words" in similar.render(POOL[:2], "en")
