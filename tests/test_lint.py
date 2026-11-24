import json
from dataclasses import replace
from pathlib import Path

import pytest

from skill.scripts import lint

profilelib = lint.profilelib
DEFAULTS = profilelib.default_rules()
NO_LIMITS = replace(
    DEFAULTS,
    commas_rate_p90=None,
    commas_per_burst_p90=None,
    words_per_burst_p90=None,
)


def codes(issues: list[lint.Issue]) -> list[str]:
    return [i.code for i in issues]


class TestHardRules:
    def test_clean_chat_line(self) -> None:
        assert lint.check("ок, гляну вечером", DEFAULTS) == []

    def test_yo(self) -> None:
        issues = lint.check("ещё раз гляну", DEFAULTS)
        assert codes(issues) == ["yo"]
        assert "ещё" in issues[0].message

    def test_yo_allowed(self) -> None:
        assert lint.check("ещё раз гляну", replace(DEFAULTS, yo="allow")) == []

    @pytest.mark.parametrize("text", ["ок — скину", "ок – скину", "ок - скину"])
    def test_dashes(self, text: str) -> None:
        assert codes(lint.check(text, DEFAULTS)) == ["dash"]

    def test_hyphenated_word_is_fine(self) -> None:
        assert lint.check("кто-то скинет", DEFAULTS) == []

    def test_emoji(self) -> None:
        assert codes(lint.check("ок 👍", replace(DEFAULTS, emoji="never"))) == ["emoji"]
        assert lint.check("ок 👍", DEFAULTS) == []

    def test_apostrophes_only_in_english(self) -> None:
        rules = replace(NO_LIMITS, en_apostrophes="never")
        assert codes(lint.check("I don't know yet", rules)) == ["apostrophe"]
        assert lint.check("I do not know yet", rules) == []
        assert lint.check("скинул в канал д'Артаньяна", rules) == []

    def test_profanity(self) -> None:
        assert codes(lint.check("блять опять упало", replace(DEFAULTS, profanity="never"))) == [
            "profanity"
        ]
        assert lint.check("блять опять упало", DEFAULTS) == []


class TestStoplist:
    @pytest.mark.parametrize(
        "text",
        [
            "Важно отметить что всё работает",
            "не секрет, что релиз съехал",
            "Надеюсь, это поможет!",
            "This works seamlessly now",
            "Let me know if you have any questions.",
        ],
    )
    def test_builtin_hits(self, text: str) -> None:
        assert "stoplist" in codes(lint.check(text, replace(NO_LIMITS, yo="allow")))

    def test_word_boundary(self) -> None:
        assert lint.check("отметить в календаре", DEFAULTS) == []

    def test_profile_stoplist(self) -> None:
        rules = replace(DEFAULTS, stoplist=("заранее спасибо",))
        assert codes(lint.check("заранее спасибо", rules)) == ["stoplist"]
        assert lint.check("заранее спасибо", DEFAULTS) == []

    def test_normalise(self) -> None:
        assert lint.normalise("  Всё,  ОК: «да» ") == "все ок да"


class TestMarkdown:
    @pytest.mark.parametrize(
        "text",
        [
            "**важно** глянь",
            "# Заголовок\nтекст",
            "> цитата",
            "смотри [тут](https://x.com)",
            "```\nок\n```",
        ],
    )
    def test_leaks(self, text: str) -> None:
        assert "markdown" in codes(lint.check(text, NO_LIMITS, platform="slack"))

    def test_discord_allows_markdown(self) -> None:
        assert lint.check("**важно** глянь", NO_LIMITS, platform="discord") == []

    def test_inline_code_and_bullets_fine(self) -> None:
        assert lint.check("поправь `config.py`\n• раз\n• два", NO_LIMITS, platform="slack") == []


class TestContrast:
    @pytest.mark.parametrize(
        "text",
        [
            "это не про деньги, а про время",
            "не потому что лень, а потому что некогда",
            "It's not about speed, it's about trust",
            "not just a fix, but a rewrite",
        ],
    )
    def test_flagged(self, text: str) -> None:
        assert "contrast" in codes(lint.check(text, NO_LIMITS))

    @pytest.mark.parametrize(
        "text", ["не знаю, а ты?", "не успел, а завтра гляну", "it is not ready"]
    )
    def test_ordinary_speech(self, text: str) -> None:
        assert "contrast" not in codes(lint.check(text, NO_LIMITS))


class TestThresholds:
    def test_comma_rate_over_p90(self) -> None:
        rules = replace(NO_LIMITS, commas_rate_p90=10.0)
        assert codes(lint.check("ну, просто, да, ладно, пока", rules)) == ["commas"]

    def test_comma_rate_at_p90_passes(self) -> None:
        rules = replace(NO_LIMITS, commas_rate_p90=10.0)
        text = "раз два три четыре пять, шесть семь восемь девять десять"
        assert lint.check(text, rules) == []

    def test_short_turn_uses_per_burst_count(self) -> None:
        rules = replace(NO_LIMITS, commas_rate_p90=10.0, commas_per_burst_p90=0.0)
        assert codes(lint.check("ну, да", rules)) == ["commas"]
        assert lint.check("ну да", rules) == []

    def test_length(self) -> None:
        rules = replace(NO_LIMITS, words_per_burst_p90=5.0)
        assert codes(lint.check("раз два три четыре пять шесть", rules)) == ["length"]
        assert lint.check("раз два три четыре пять", rules) == []

    def test_messages(self) -> None:
        rules = replace(NO_LIMITS, messages_per_burst_p90=2.0)
        assert codes(lint.check("раз\nдва\nтри", rules)) == ["messages"]
        assert lint.check("раз\n\nдва", rules) == []

    def test_capital_for_lowercase_writer(self) -> None:
        rules = replace(NO_LIMITS, capital_start_share=0.1)
        assert codes(lint.check("Привет\nКак дела", rules)) == ["capital"]
        assert lint.check("привет\nкак дела", rules) == []

    def test_capital_one_proper_name_is_fine(self) -> None:
        rules = replace(NO_LIMITS, capital_start_share=0.1)
        assert lint.check("Костя звонил\nнабери его\nон ждет", rules) == []

    def test_capital_for_sentence_case_writer(self) -> None:
        rules = replace(NO_LIMITS, capital_start_share=0.9)
        assert codes(lint.check("привет\nкак дела", rules)) == ["capital"]

    def test_period(self) -> None:
        rules = replace(NO_LIMITS, period_end_share=0.05)
        assert codes(lint.check("ок.", rules)) == ["period"]
        assert lint.check("ок", rules) == []
        assert lint.check("ок...", rules) == []

    def test_doc_mode_skips_thresholds(self) -> None:
        rules = replace(NO_LIMITS, words_per_burst_p90=2.0, capital_start_share=0.0)
        assert lint.check("Длинный нормальный текст документа", rules, mode="doc") == []

    def test_default_comma_limit(self) -> None:
        text = "ну, вот, так, и, да, нет, ок, ладно"
        assert "commas" in codes(lint.check(text, DEFAULTS))


def _profile(tmp_path: Path, registers: dict[str, dict], settings: str = "") -> Path:
    stats = {"min_words_for_rate": 4, "registers": registers}
    (tmp_path / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    if settings:
        (tmp_path / "settings.toml").write_text(settings, encoding="utf-8")
    return tmp_path


class TestPickRules:
    def test_no_profile(self, tmp_path: Path) -> None:
        rules, source = lint.pick_rules(tmp_path, None, "привет")
        assert rules == DEFAULTS
        assert "defaults" in source

    def test_register_by_language(self, tmp_path: Path) -> None:
        path = _profile(tmp_path, {"friends": {"lang": "ru"}, "clients": {"lang": "en"}})
        assert lint.pick_rules(path, None, "привет как ты")[0].register == "friends"
        assert lint.pick_rules(path, None, "hi there")[0].register == "clients"

    def test_ambiguous(self, tmp_path: Path) -> None:
        path = _profile(tmp_path, {"friends": {"lang": "ru"}, "work": {"lang": "ru"}})
        with pytest.raises(profilelib.ProfileError, match="--register"):
            lint.pick_rules(path, None, "привет")

    def test_unknown_register(self, tmp_path: Path) -> None:
        path = _profile(tmp_path, {"friends": {"lang": "ru"}})
        with pytest.raises(profilelib.ProfileError, match="unknown register"):
            lint.pick_rules(path, "nope", "привет")

    def test_min_words_from_top_level(self, tmp_path: Path) -> None:
        path = _profile(tmp_path, {"friends": {"lang": "ru"}})
        assert lint.pick_rules(path, "friends", "x")[0].min_words == 4


class TestMain:
    def test_exit_codes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        clean = tmp_path / "a.txt"
        clean.write_text("ок, гляну", encoding="utf-8")
        dirty = tmp_path / "b.txt"
        dirty.write_text("ещё — гляну", encoding="utf-8")
        empty = tmp_path / "c.txt"
        empty.write_text("  \n", encoding="utf-8")
        assert lint.main([str(clean), "--no-profile"]) == 0
        assert lint.main([str(dirty), "--no-profile"]) == 1
        out = capsys.readouterr().out
        assert "2 error(s)" in out
        assert lint.main([str(empty), "--no-profile"]) == 2

    def test_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src = tmp_path / "a.txt"
        src.write_text("ещё", encoding="utf-8")
        lint.main([str(src), "--no-profile", "--json"])
        data = json.loads(capsys.readouterr().out)
        assert [i["code"] for i in data["issues"]] == ["yo"]


class TestReviewFixes:
    @pytest.mark.parametrize("text", ["ну да -- завтра", "ну да ‒ завтра", "ну да ― завтра"])
    def test_more_dashes(self, text: str) -> None:
        assert "dash" in codes(lint.check(text, NO_LIMITS))

    def test_schemeless_markdown_link(self) -> None:
        assert "markdown" in codes(lint.check("[тут](t.me/x)", NO_LIMITS, platform="telegram"))

    @pytest.mark.parametrize("platform", ["telegram", "plain", "email"])
    def test_emphasis_symbols_outside_slack(self, platform: str) -> None:
        assert "markdown" in codes(lint.check("это *важно*", NO_LIMITS, platform=platform))
        assert "markdown" in codes(lint.check("это ~~старое~~", NO_LIMITS, platform=platform))

    def test_slack_single_asterisk_is_bold(self) -> None:
        assert lint.check("это *важно*", NO_LIMITS, platform="slack") == []

    def test_missing_draft_file(self, tmp_path: Path) -> None:
        assert lint.main([str(tmp_path / "nope.txt"), "--no-profile"]) == 2

    def test_broken_stats(self, tmp_path: Path) -> None:
        (tmp_path / "stats.json").write_text("{not json")
        draft = tmp_path / "d.txt"
        draft.write_text("ок")
        assert lint.main([str(draft), "--profile", str(tmp_path)]) == 2

    @pytest.mark.parametrize("settings", ['rules = "x"', "stoplist = []", 'registers = "a"'])
    def test_malformed_settings(self, tmp_path: Path, settings: str) -> None:
        _profile(tmp_path, {"r": {"lang": "ru"}}, settings + "\n")
        draft = tmp_path / "d.txt"
        draft.write_text("ок")
        assert lint.main([str(draft), "--profile", str(tmp_path)]) == 2


class TestLengthAgainstSimilar:
    @pytest.fixture
    def prof(self, tmp_path: Path) -> Path:
        path = _profile(tmp_path, {"friends": {"lang": "ru"}})
        reg = path / "registers" / "friends"
        reg.mkdir(parents=True)
        pool = [
            {"in": ["го в кино завтра"], "out": ["го"]},
            {"in": ["кино вечером"], "out": ["давай"]},
        ]
        (reg / "pairs.jsonl").write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in pool))
        (tmp_path / "in.txt").write_text("го в кино?")
        return path

    def _run(self, prof: Path, draft: str, capsys: pytest.CaptureFixture[str]) -> list[str]:
        d = prof / "draft.txt"
        d.write_text(draft)
        lint.main(
            [
                str(d),
                "--profile",
                str(prof),
                "--register",
                "friends",
                "--incoming",
                str(prof / "in.txt"),
                "--json",
            ]
        )
        return [i["code"] for i in json.loads(capsys.readouterr().out)["issues"]]

    def test_long_draft_warned(self, prof: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert "similar_length" in self._run(prof, "да го конечно я только за с радостью", capsys)

    def test_short_draft_fine(self, prof: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._run(prof, "го", capsys) == []

    def test_small_overshoot_fine(self, prof: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._run(prof, "го на что", capsys) == []

    def test_no_pool_no_crash(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = _profile(tmp_path, {"friends": {"lang": "ru"}})
        (tmp_path / "in.txt").write_text("го")
        d = tmp_path / "d.txt"
        d.write_text("го")
        assert (
            lint.main(
                [
                    str(d),
                    "--profile",
                    str(path),
                    "--register",
                    "friends",
                    "--incoming",
                    str(tmp_path / "in.txt"),
                ]
            )
            == 0
        )
