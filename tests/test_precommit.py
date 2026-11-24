import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "githooks" / "pre-commit"


def _run_hook(repo: Path, files: list[str]) -> int:
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-f", *files], check=True)
    return subprocess.run(["bash", str(HOOK)], cwd=repo, capture_output=True).returncode


@pytest.mark.parametrize(
    "path",
    [
        "data/corpus/telegram.jsonl",
        "data/корпус.jsonl",
        "profiles/me/profile.md",
        "profiles/вася/profile.md",
        "skill/profile/profile.md",
        "dist/humanize.zip",
        "humanize.zip",
    ],
)
def test_private_paths_blocked(tmp_path: Path, path: str) -> None:
    assert _run_hook(tmp_path, [path]) == 1


@pytest.mark.parametrize(
    "path",
    ["profiles/example/profile.md", "pipeline/stats.py", "skill/SKILL.md", "README.md"],
)
def test_public_paths_pass(tmp_path: Path, path: str) -> None:
    assert _run_hook(tmp_path, [path]) == 0
