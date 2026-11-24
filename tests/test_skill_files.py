import re
from pathlib import Path

import pytest

from skill.scripts import lint
from skill.scripts.to_slack import to_slack

SKILL = Path(__file__).resolve().parent.parent / "skill"
TEXT_FILES = sorted(
    p for p in SKILL.rglob("*") if p.suffix in {".md", ".txt"} and "profile" not in p.parts
)


def _frontmatter(text: str) -> dict[str, str]:
    block = text.split("---")[1]
    name = re.search(r"^name:\s*(\S+)", block, re.M)
    desc = re.search(r"^description:\s*>-\n((?:  .*\n?)+)", block, re.M)
    assert name and desc
    return {"name": name.group(1), "description": " ".join(desc.group(1).split())}


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.name)
def test_no_yo_in_skill_text(path: Path) -> None:
    # The model copies what it reads; a rule file written with the letter teaches it back.
    assert "ё" not in path.read_text(encoding="utf-8").lower()


def test_frontmatter_limits() -> None:
    meta = _frontmatter((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    assert meta["name"] == "humanize"
    assert len(meta["description"]) <= 1024


def test_short_description_fits_claude_ai() -> None:
    short = (SKILL / "description-short.txt").read_text(encoding="utf-8").strip()
    assert 0 < len(short) <= 200


def test_referenced_files_exist() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for ref in re.findall(r"`((?:references|scripts)/[\w.-]+)`", text):
        assert (SKILL / ref).exists(), ref


def test_stoplist_has_both_languages() -> None:
    phrases = lint.load_stoplist()
    assert any(re.search("[а-я]", p) for p in phrases)
    assert any(re.search("[a-z]", p) for p in phrases)
    assert all(p == p.strip() and p for p in phrases)


class TestToSlack:
    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("**важно** глянь", "*важно* глянь"),
            ("## План\nтекст", "*План*\nтекст"),
            ("[тикет](https://x.com/1)", "<https://x.com/1|тикет>"),
            ("~~старое~~", "~старое~"),
            ("- раз\n* два", "• раз\n• два"),
            ("> цитата", "цитата"),
            ("`**не трогать**`", "`**не трогать**`"),
            ("```\n# код\n```", "```\n# код\n```"),
            ("*уже mrkdwn*", "*уже mrkdwn*"),
            ("", ""),
        ],
    )
    def test_convert(self, src: str, expected: str) -> None:
        assert to_slack(src) == expected

    def test_output_passes_lint_markdown_check(self) -> None:
        rules = lint.profilelib.default_rules()
        out = to_slack("## Итог\n**готово**\n- раз\n> цитата\n[тут](https://x.com)")
        assert "markdown" not in [i.code for i in lint.check(out, rules, platform="slack")]
