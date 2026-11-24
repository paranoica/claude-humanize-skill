import pytest

from skill.scripts import textmetrics as tm


class TestClean:
    def test_url_removed_but_trailing_comma_kept(self) -> None:
        text = "глянь https://example.com/a?b=1, норм"
        assert tm.words(text) == ["глянь", "норм"]
        assert tm.count_commas(text) == 1

    def test_inline_code_removed(self) -> None:
        text = "поправил `foo, bar` в конфиге"
        assert tm.words(text) == ["поправил", "в", "конфиге"]
        assert tm.count_commas(text) == 0

    def test_fenced_code_removed(self) -> None:
        text = "вот так:\n```\nx = f(a, b)\n```\nработает"
        assert tm.words(text) == ["вот", "так", "работает"]
        assert tm.count_commas(text) == 0

    def test_mentions_and_shortcodes_removed(self) -> None:
        assert tm.words("@vasya :thumbsup: ок") == ["ок"]


class TestWords:
    def test_hyphen_apostrophe_and_digits(self) -> None:
        assert tm.words("кто-то сказал it's ок, 42 раза") == [
            "кто-то",
            "сказал",
            "it's",
            "ок",
            "раза",
        ]

    def test_empty(self) -> None:
        assert tm.words("") == []


class TestDetectLang:
    @pytest.mark.parametrize(
        ("text", "lang"),
        [
            ("задеплоил на prod", "ru"),
            ("deployed to прод", "en"),
            ("👍", "other"),
            ("", "other"),
            ("ok ок", "other"),
        ],
    )
    def test_detect(self, text: str, lang: str) -> None:
        assert tm.detect_lang(text) == lang


class TestCommas:
    @pytest.mark.parametrize(
        ("text", "n"),
        [("ну, просто, да", 2), ("версия 1,5 ок", 0), ("было 5, стало 6", 1), ("", 0)],
    )
    def test_count(self, text: str, n: int) -> None:
        assert tm.count_commas(text) == n


class TestEnding:
    @pytest.mark.parametrize(
        ("text", "kind"),
        [
            ("ок.", "period"),
            ("ок...", "ellipsis"),
            ("ок…", "ellipsis"),
            ("ок?", "question"),
            ("ок!", "exclamation"),
            ("ок)", "bracket"),
            ("ок 👍", "emoji"),
            ("ок 👍🏻", "emoji"),
            ("ок ❤️", "emoji"),
            ("👍:", "other"),
            ("ок", "none"),
            ("ок:", "other"),
            ("", "empty"),
            ("   ", "empty"),
        ],
    )
    def test_kind(self, text: str, kind: str) -> None:
        assert tm.ending(text) == kind


class TestStartsUpper:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Привет", True),
            ("привет", False),
            ("123 штуки", None),
            ("", None),
            ("  @vasya Привет", True),
        ],
    )
    def test_starts_upper(self, text: str, expected: bool | None) -> None:
        assert tm.starts_upper(text) is expected


class TestDashes:
    def test_all_kinds(self) -> None:
        assert tm.dash_counts("а — б – в - г") == {"em": 1, "en": 1, "spaced_hyphen": 1}

    def test_word_hyphen_is_not_dash(self) -> None:
        assert tm.count_dashes("кто-то где-то") == 0

    def test_list_bullets_are_not_dashes(self) -> None:
        assert tm.count_dashes("список:\n- раз\n- два") == 0


class TestApostrophes:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("it's fine", True),
            ("it’s fine", True),
            ("it is fine", False),
            ("'quoted'", False),
        ],
    )
    def test_has_apostrophe(self, text: str, expected: bool) -> None:
        assert tm.has_apostrophe(text) is expected

    def test_negations(self) -> None:
        text = "I don't know, it is not ready and we cannot ship, won't"
        assert tm.negation_counts(text) == (2, 2)

    def test_negations_none(self) -> None:
        assert tm.negation_counts("all good") == (0, 0)


class TestEmoji:
    def test_sequences_count_once(self) -> None:
        assert len(tm.emojis("ок 👍🏻 и 🇷🇺 и 👨‍👩‍👧")) == 3

    def test_shortcode(self) -> None:
        assert tm.emojis(":fire: ок") == [":fire:"]

    def test_time_is_not_shortcode(self) -> None:
        assert tm.emojis("время 10:30:15") == []


class TestBracketSmileys:
    @pytest.mark.parametrize(
        ("text", "n"),
        [
            ("привет))", 1),
            ("(см. выше)", 0),
            ("ок) (в смысле да)", 1),
            ("грустно((", 1),
            ("привет)) как ты(", 2),
            (":)", 1),
            ("", 0),
        ],
    )
    def test_count(self, text: str, n: int) -> None:
        assert tm.bracket_smileys(text) == n


class TestProfanity:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("блять опять", True),
            ("это пиздец", True),
            ("fucking bug", True),
            ("небо требует", False),
            ("хуже некуда", False),
            ("", False),
        ],
    )
    def test_has_profanity(self, text: str, expected: bool) -> None:
        assert tm.has_profanity(text) is expected


class TestYo:
    def test_count_yo_words(self) -> None:
        assert tm.count_yo_words(["ещё", "еще", "всё"]) == 2

    def test_known_counts(self) -> None:
        assert tm.yo_known_counts(["ещё", "еще", "идёт", "все"]) == (2, 3)


class TestDistribution:
    def test_empty(self) -> None:
        assert tm.distribution([]) == {"n": 0, "mean": None, "median": None, "p90": None}

    def test_single(self) -> None:
        assert tm.distribution([5]) == {"n": 1, "mean": 5, "median": 5, "p90": 5}

    def test_one_to_ten(self) -> None:
        d = tm.distribution(list(range(1, 11)))
        assert d["n"] == 10
        assert d["mean"] == 5.5
        assert d["median"] == 5.5
        assert d["p90"] == pytest.approx(9.1)
