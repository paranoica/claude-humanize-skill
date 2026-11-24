"""The README walkthrough on the example data, run as a user would run it."""

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "profiles" / "example"


def _run(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=REPO, input=stdin, capture_output=True, text=True
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("e2e")
    profile = root / "misha"
    profile.mkdir()
    for name in ("corpus.toml", "settings.toml"):
        shutil.copy(EXAMPLE / name, profile / name)
    corpus = root / "corpus"
    steps = [
        (
            "-m", "pipeline.ingest_telegram", "examples/telegram/result.json",
            "-o", f"{corpus}/t.jsonl",
        ),
        ("-m", "pipeline.stats", "chats", "--corpus", str(corpus)),
        (
            "-m", "pipeline.build_profile", "--profile", str(profile), "--corpus", str(corpus),
            "--bank-size", "30", "--holdout", "9",
        ),
    ]  # fmt: skip
    for step in steps:
        result = _run(*step)
        assert result.returncode == 0, result.stderr
    return profile


def test_example_export_is_reproducible() -> None:
    sys.path.insert(0, str(REPO / "examples"))
    import generate_example

    committed = json.loads((REPO / "examples" / "telegram" / "result.json").read_text())
    assert committed == generate_example.generate()


def test_profile_files(built: Path) -> None:
    for reg in ("friends", "work", "clients"):
        examples = (built / "registers" / reg / "examples.md").read_text(encoding="utf-8")
        assert examples.count("\n## ") >= 30
    holdout = (built / "holdout.jsonl").read_text().splitlines()
    assert len(holdout) == 9


def test_committed_example_matches_a_fresh_build(built: Path) -> None:
    fresh = json.loads((built / "stats.json").read_text())
    committed = json.loads((EXAMPLE / "stats.json").read_text())
    assert fresh["registers"] == committed["registers"]
    for reg in ("friends", "work", "clients"):
        for name in ("facts.md", "examples.md"):
            a = (built / "registers" / reg / name).read_text(encoding="utf-8")
            b = (EXAMPLE / "registers" / reg / name).read_text(encoding="utf-8")
            assert a == b, f"{reg}/{name} is stale; rebuild profiles/example"


@pytest.mark.parametrize(
    ("register", "draft", "codes"),
    [
        ("friends", "норм\nвечером скину", []),
        ("friends", "Привет! Да, конечно, скину вечером.", ["commas", "capital", "period"]),
        ("work", "еще нет\nтесты падают на миграции", []),
        ("work", "ещё нет — тесты падают", ["yo", "dash"]),
        ("clients", "Sure, that works.\nI'll send it on Thursday.", []),
        ("clients", "Let's circle back on this.", ["stoplist"]),
    ],
)
def test_lint_with_profile(built: Path, register: str, draft: str, codes: list[str]) -> None:
    args = ["skill/scripts/lint.py", "--profile", str(built), "--register", register, "--json"]
    result = _run(*args, stdin=draft)
    found = [i["code"] for i in json.loads(result.stdout)["issues"]]
    assert sorted(found) == sorted(codes)
    assert result.returncode == (1 if codes else 0)


def test_package(built: Path, tmp_path: Path) -> None:
    out = tmp_path / "h.zip"
    result = _run("-m", "pipeline.package", "--profile", str(built), "-o", str(out))
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        skill_md = zf.read("humanize/SKILL.md").decode()
    assert "humanize/profile/registers/clients/examples.md" in names
    assert not any(n.endswith(("holdout.jsonl", "corpus.toml")) for n in names)
    assert 'description: "Writes messages' in skill_md
