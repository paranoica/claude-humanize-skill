import pytest

from skill.scripts import profilelib as pl


def _settings(**rules: str) -> dict:
    return pl.validate_settings({"rules": rules} if rules else {})


class TestValidate:
    def test_defaults(self) -> None:
        s = pl.validate_settings({})
        assert s["rules"]["yo"] == "auto"
        assert s["default_platform"] == "plain"

    @pytest.mark.parametrize(
        ("data", "match"),
        [
            ({"colour": 1}, "unknown key"),
            ({"rules": {"yo": "sometimes"}}, "settings.rules.yo"),
            ({"rules": {"ёё": "never"}}, "unknown rule"),
            ({"default_platform": "icq"}, "default_platform"),
            ({"stoplist": {"ru": "одна строка"}}, "stoplist.ru"),
            ({"registers": {"friends": {"emoji": "maybe"}}}, "registers.friends.emoji"),
        ],
    )
    def test_rejects(self, data: dict, match: str) -> None:
        with pytest.raises(pl.ProfileError, match=match):
            pl.validate_settings(data)


class TestAutoRules:
    @pytest.mark.parametrize(
        ("yo", "expected"),
        [
            ({"known_with_yo": 0, "known_total": 37, "word_share": 0.0}, "never"),
            ({"known_with_yo": 3, "known_total": 37, "word_share": 0.001}, "never"),
            ({"known_with_yo": 4, "known_total": 37, "word_share": 0.002}, "allow"),
            ({"known_with_yo": 0, "known_total": 3, "word_share": 0.02}, "allow"),
            ({"known_with_yo": 0, "known_total": 0, "word_share": 0.0}, "never"),
        ],
    )
    def test_yo(self, yo: dict, expected: str) -> None:
        assert pl.resolve(_settings(), {"yo": yo}, "r").yo == expected

    @pytest.mark.parametrize(
        ("share", "expected"), [(0.0, "never"), (0.019, "never"), (0.02, "allow")]
    )
    def test_dashes_and_emoji(self, share: float, expected: str) -> None:
        stats = {"dashes": {"message_share": share}, "emoji": {"message_share": share}}
        rules = pl.resolve(_settings(), stats, "r")
        assert (rules.dashes, rules.emoji) == (expected, expected)

    @pytest.mark.parametrize(
        ("messages", "share", "expected"),
        [(19, 0.0, "allow"), (20, 0.0, "never"), (50, 0.049, "never"), (50, 0.05, "allow")],
    )
    def test_en_apostrophes(self, messages: int, share: float, expected: str) -> None:
        stats = {"en": {"messages": messages, "apostrophe_message_share": share}}
        assert pl.resolve(_settings(), stats, "r").en_apostrophes == expected

    def test_thresholds_from_stats(self) -> None:
        stats = {
            "lang": "ru",
            "commas": {"per_100_words_per_burst": {"p90": 8.0}, "per_burst": {"p90": 1.0}},
            "words_per_burst": {"p90": 18.0},
            "messages_per_burst": {"p90": 3.0},
            "capital_start_share": 0.16,
            "ending": {"period": 0.03},
        }
        rules = pl.resolve(_settings(), stats, "friends")
        assert rules.lang == "ru"
        assert rules.commas_rate_p90 == 8.0
        assert rules.commas_per_burst_p90 == 1.0
        assert rules.words_per_burst_p90 == 18.0
        assert rules.messages_per_burst_p90 == 3.0
        assert rules.capital_start_share == 0.16
        assert rules.period_end_share == 0.03


class TestOverrides:
    def test_global_setting_beats_auto(self) -> None:
        stats = {"yo": {"known_with_yo": 0, "known_total": 50}}
        rules = pl.resolve(_settings(yo="allow"), stats, "r")
        assert rules.yo == "allow"
        assert rules.sources["yo"] == "set in settings"

    def test_register_override_beats_global(self) -> None:
        settings = pl.validate_settings(
            {"rules": {"emoji": "never"}, "registers": {"friends": {"emoji": "allow"}}}
        )
        assert pl.resolve(settings, {}, "friends").emoji == "allow"
        assert pl.resolve(settings, {}, "work").emoji == "never"

    def test_stoplist_merged(self) -> None:
        settings = pl.validate_settings({"stoplist": {"ru": ["заранее спасибо"], "en": ["kindly"]}})
        assert pl.resolve(settings, {}, "r").stoplist == ("заранее спасибо", "kindly")


class TestDefaults:
    def test_no_profile(self) -> None:
        rules = pl.default_rules()
        assert (rules.yo, rules.dashes, rules.emoji) == ("never", "never", "allow")
        assert rules.commas_rate_p90 == pl.DEFAULT_COMMAS_RATE_P90
        assert rules.capital_start_share is None

    def test_missing_stats(self, tmp_path) -> None:
        with pytest.raises(pl.ProfileError, match=r"stats\.json"):
            pl.load_profile(tmp_path)


class TestSettingsFallback:
    STATS = '{"registers": {"r": {"lang": "ru"}}}'

    def test_json_used_without_tomllib(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "stats.json").write_text(self.STATS)
        (tmp_path / "settings.toml").write_text('[rules]\nemoji = "never"\n')
        (tmp_path / "settings.json").write_text('{"rules": {"emoji": "never"}}')
        monkeypatch.setattr(pl, "tomllib", None)
        assert pl.load_profile(tmp_path).rules("r").emoji == "never"

    def test_toml_preferred(self, tmp_path) -> None:
        (tmp_path / "stats.json").write_text(self.STATS)
        (tmp_path / "settings.toml").write_text('[rules]\nemoji = "allow"\n')
        (tmp_path / "settings.json").write_text('{"rules": {"emoji": "never"}}')
        assert pl.load_profile(tmp_path).rules("r").emoji == "allow"

    def test_toml_without_tomllib_and_no_json(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "stats.json").write_text(self.STATS)
        (tmp_path / "settings.toml").write_text("owner = 'x'\n")
        monkeypatch.setattr(pl, "tomllib", None)
        with pytest.raises(pl.ProfileError, match=r"3\.11"):
            pl.load_profile(tmp_path)
