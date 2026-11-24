import json
from pathlib import Path

import pytest

from bench import report, run
from hooks import draft_trigger
from pipeline import build_profile
from tests.test_build_profile import _corpus


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    prof = tmp_path / "me"
    build_profile.build(prof, _corpus(), holdout_size=4, seed=3)
    (prof / "corpus.toml").write_text('[[register]]\nname = "ru"\nlang = "ru"\n')
    return prof


def test_prompt_is_a_draft_request(profile: Path) -> None:
    case = run.load_cases(profile / "holdout.jsonl")[0]
    prompt = run.build_prompt(case)
    assert prompt.startswith("Ответь от моего имени в Telegram. Регистр: ru.")
    assert case.context[0][1] in prompt
    assert draft_trigger.wants_message(prompt)
    assert "Регистр" not in run.build_prompt(case, with_register=False)


def test_en_prompt() -> None:
    case = run.Case(1, "k", "clients", "en", "slack", [("A", "any update?")], ["soon"])
    prompt = run.build_prompt(case)
    assert prompt == "Reply as me on Slack. Register: clients.\n\nIncoming:\n[A] any update?"
    assert draft_trigger.wants_message(prompt)


def test_home_has_skill_profile_and_hook_only(tmp_path: Path, profile: Path) -> None:
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    home, cwd = run.make_home(tmp_path / "job", profile, creds)
    skill = home / ".claude" / "skills" / "humanize"
    assert (skill / "SKILL.md").is_file()
    assert (skill / "scripts" / "lint.py").is_file()
    assert (skill / "profile" / "registers" / "ru" / "examples.md").is_file()
    assert not (skill / "profile" / "holdout.jsonl").exists()
    assert not (skill / "profile" / "corpus.toml").exists()
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert (home / ".claude" / ".credentials.json").is_symlink()
    settings = json.loads((home / ".claude" / "settings.json").read_text())
    assert settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"].endswith(
        "draft_trigger.sh"
    )
    assert cwd.is_dir()


def test_home_without_profile(tmp_path: Path) -> None:
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    home, _ = run.make_home(tmp_path / "job", None, creds)
    assert not (home / ".claude" / "skills" / "humanize" / "profile").exists()


def test_command_puts_prompt_first() -> None:
    cmd = run.build_command("ответь ему", model="opus")
    assert cmd[:3] == ["claude", "-p", "ответь ему"]
    assert cmd[-2:] == ["--model", "opus"]
    assert "Bash(python3:*)" in cmd


def test_parse_stream_output() -> None:
    events = [
        {"type": "system", "subtype": "hook_response", "output": '{"additionalContext": "x"}'},
        {"type": "system", "subtype": "permission_denied", "tool": "Bash"},
        {"type": "system", "subtype": "init"},
        {"type": "user", "message": "plain string"},
        {"type": "assistant", "message": {"content": "text only"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "humanize"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "python3 x/lint.py d"}},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "d"}}]}},
        {"type": "result", "result": " ок \n", "num_turns": 3, "total_cost_usd": 0.1},
    ]  # fmt: skip
    out = run.parse_output("\n".join(json.dumps(e) for e in events) + "\nnoise")
    assert out["result"] == "ок"
    assert out["skills"] == ["humanize"]
    assert out["lint_runs"] == 1
    assert out["similar_runs"] == 0
    assert out["tools"] == ["Skill", "Bash", "Write"]
    assert out["hook_context"] is True
    assert len(out["denied"]) == 1
    assert run.parse_output(json.dumps(events[-1]))["hook_context"] is False
    with pytest.raises(ValueError):
        run.parse_output("oops")


def test_report_and_blind_page(tmp_path: Path, profile: Path) -> None:
    results = tmp_path / "res"
    results.mkdir()
    cases = run.load_cases(profile / "holdout.jsonl")
    k1, k2 = cases[0].key, cases[1].key
    (results / "case-01.json").write_text(
        json.dumps({"key": k1, "result": "ещё <b>, гляну", "error": None})
    )
    (results / "case-02.json").write_text(json.dumps({"key": k2, "result": "", "error": "exit 1"}))
    rows = report.collect(results, profile)
    assert len(rows) == 2
    acc = report.acceptance(rows)
    assert (acc["answered"], acc["no_yo"]) == (1, 0)
    page = report.render_blind(rows)
    assert "ещё &lt;b&gt;, гляну" in page
    assert cases[0].reply[0] in page
    answers = json.loads(page.split("const ANS=")[1].split(";")[0])
    assert list(answers) == ["1"]
    assert "| 2 | ru |" in report.render_report(rows)


class TestCredentials:
    def _setup(self, tmp_path: Path, real: dict, fresh: object) -> tuple[Path, Path]:
        creds = tmp_path / "real.json"
        creds.write_text(json.dumps(real))
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / ".credentials.json").write_text(
            fresh if isinstance(fresh, str) else json.dumps(fresh)
        )
        return home, creds

    def test_newer_token_copied_back(self, tmp_path: Path) -> None:
        real = {"claudeAiOauth": {"accessToken": "old", "expiresAt": 100}}
        fresh = {"claudeAiOauth": {"accessToken": "new", "expiresAt": 200}}
        home, creds = self._setup(tmp_path, real, fresh)
        assert run.sync_credentials(home, creds)
        assert json.loads(creds.read_text())["claudeAiOauth"]["accessToken"] == "new"

    @pytest.mark.parametrize(
        "fresh",
        ['""', "{}", "not json", {"claudeAiOauth": {"accessToken": "older", "expiresAt": 50}}],
    )
    def test_invalid_or_older_token_ignored(self, tmp_path: Path, fresh: object) -> None:
        real = {"claudeAiOauth": {"accessToken": "old", "expiresAt": 100}}
        home, creds = self._setup(tmp_path, real, fresh)
        assert run.sync_credentials(home, creds) is None
        assert json.loads(creds.read_text()) == real


def test_results_matched_by_key(tmp_path: Path, profile: Path) -> None:
    results = tmp_path / "res"
    results.mkdir()
    (results / "case-01.json").write_text(
        json.dumps({"key": "stale", "result": "x", "error": None})
    )
    assert report.collect(results, profile)[0]["generated"] == ""
    assert not run._done(results / "case-01.json", "fresh-key")


def test_blind_page_does_not_leak_answers(tmp_path: Path, profile: Path) -> None:
    results = tmp_path / "res"
    results.mkdir()
    key = run.load_cases(profile / "holdout.jsonl")[0].key
    (results / "case-01.json").write_text(
        json.dumps({"key": key, "result": "{{ANSWERS}}", "error": None})
    )
    page = report.render_blind(report.collect(results, profile))
    assert page.count("const ANS=") == 1
    assert "{{ANSWERS}}" in page.split("<script>")[0]


def test_default_prompts_file() -> None:
    cases = run.load_prompts(run.REPO / "bench" / "default_prompts.json")
    assert len(cases) == 10
    assert {c.lang for c in cases} == {"ru", "en"}
    assert all(run.build_prompt(c) == c.prompt for c in cases)
    assert all(draft_trigger.wants_message(c.prompt) for c in cases)


def test_default_summary(tmp_path: Path) -> None:
    prompts = tmp_path / "p.json"
    prompts.write_text(json.dumps([
        {"id": "a", "prompt": "ответь ему", "platform": "slack"},
        {"id": "b", "prompt": "reply to him", "platform": "slack"},
    ]))  # fmt: skip
    (tmp_path / "case-01.json").write_text(json.dumps({"result": "ок гляну"}))
    (tmp_path / "case-02.json").write_text(json.dumps({"result": "Sure — on it"}))
    text = report.default_summary(tmp_path, prompts)
    assert "Lint clean: 1/2" in text
    assert "a (slack, chat): clean" in text
    assert "b (slack, chat): dash" in text


FENCE = "```"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"{FENCE}\nможно\n{FENCE}\n\n{FENCE}\nа на что\n{FENCE}", "можно\nа на что"),
        (f"\n{FENCE}\nок\n{FENCE}\n{FENCE}\nгляну\n{FENCE}\n", "ок\nгляну"),
        (f"{FENCE}\nок гляну\n{FENCE}", f"{FENCE}\nок гляну\n{FENCE}"),
        (f"{FENCE}\nможно\n{FENCE}\n{FENCE}\nи во скок\n{FENCE}", "можно\nи во скок"),
        ("можно\nа на что", "можно\nа на что"),
        ("", ""),
        (f"смотри:\n{FENCE}\nmake test\n{FENCE}", f"смотри:\n{FENCE}\nmake test\n{FENCE}"),
        (f"{FENCE}python\nx = 1\n{FENCE}", f"{FENCE}python\nx = 1\n{FENCE}"),
        (f"{FENCE}\nнезакрытый", f"{FENCE}\nнезакрытый"),
    ],
)
def test_unwrap_message_blocks(text: str, expected: str) -> None:
    assert report.unwrap(text) == expected


def test_report_counts_each_block_as_a_message(tmp_path: Path, profile: Path) -> None:
    results = tmp_path / "res"
    results.mkdir()
    key = run.load_cases(profile / "holdout.jsonl")[0].key
    fenced = f"{FENCE}\nможно\n{FENCE}\n\n{FENCE}\nа на что\n{FENCE}"
    (results / "case-01.json").write_text(json.dumps({"key": key, "result": fenced}))
    row = report.collect(results, profile)[0]
    assert row["generated"] == "можно\nа на что"
    assert row["gen_m"]["messages"] == 2
    assert "markdown" not in row["gen_m"]["codes"]
    assert FENCE not in report.render_blind([row])


def test_default_summary_unwraps_blocks(tmp_path: Path) -> None:
    prompts = tmp_path / "p.json"
    prompts.write_text(json.dumps([{"id": "a", "prompt": "ответь ему", "platform": "telegram"}]))
    fenced = f"{FENCE}\nок\n{FENCE}\n\n{FENCE}\nгляну вечером\n{FENCE}"
    (tmp_path / "case-01.json").write_text(json.dumps({"result": fenced}))
    assert "a (telegram, chat): clean" in report.default_summary(tmp_path, prompts)
