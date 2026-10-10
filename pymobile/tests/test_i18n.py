"""Tests for internationalisation."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pymobile import format_date
from pymobile.core.bridge import StubBridge, reset_bridge, set_bridge
from pymobile.core.i18n import Translations, device_language, normalise_language


@pytest.fixture
def catalogue() -> Translations:
    translations = Translations(default_language="en")
    translations.load({"greeting": "Hello, {name}!", "bye": "Bye"}, language="en")
    translations.load({"greeting": "Привіт, {name}!"}, language="uk")
    return translations


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("uk", "uk"),
            ("uk_UA", "uk-ua"),
            ("uk_UA.UTF-8", "uk-ua"),
            ("pt-BR", "pt-br"),
            ("en_US@euro", "en-us"),
            ("C", "en"),
            ("POSIX", "en"),
            ("", ""),
        ],
    )
    def test_tags_are_normalised(self, raw: str, expected: str) -> None:
        assert normalise_language(raw) == expected


class TestLookup:
    def test_translates_and_interpolates(self, catalogue: Translations) -> None:
        catalogue.use("uk")
        assert catalogue.get("greeting", name="Оксана") == "Привіт, Оксана!"

    def test_falls_back_to_the_bare_language(self, catalogue: Translations) -> None:
        catalogue.use("uk-UA")  # only "uk" is loaded
        assert catalogue.get("greeting", name="Ivan") == "Привіт, Ivan!"

    def test_falls_back_to_the_default_language(self, catalogue: Translations) -> None:
        catalogue.use("uk")
        assert catalogue.get("bye") == "Bye"  # only present in English

    def test_missing_key_returns_itself(self, catalogue: Translations) -> None:
        assert catalogue.get("totally.unknown") == "totally.unknown"

    def test_missing_key_can_have_a_default(self, catalogue: Translations) -> None:
        assert catalogue.get("unknown", default="Fallback") == "Fallback"

    def test_missing_key_is_logged_once(
        self, catalogue: Translations, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        catalogue.get("gone")
        catalogue.get("gone")
        assert capsys.readouterr().err.count("missing translation") == 1

    def test_bad_placeholder_does_not_raise(self, catalogue: Translations) -> None:
        catalogue.load({"broken": "Hi {nope}"}, language="en")
        assert catalogue.get("broken", name="x") == "Hi {nope}"

    def test_has_and_contains(self, catalogue: Translations) -> None:
        assert catalogue.has("greeting")
        assert "greeting" in catalogue
        assert "nope" not in catalogue

    def test_languages_are_listed(self, catalogue: Translations) -> None:
        assert catalogue.languages == ("en", "uk")

    def test_use_returns_the_selected_tag(self, catalogue: Translations) -> None:
        assert catalogue.use("PT_br") == "pt-br"

    def test_clear_resets(self, catalogue: Translations) -> None:
        catalogue.clear()
        assert catalogue.languages == ()
        assert catalogue.language == "en"


class TestPlurals:
    def test_english_rules(self) -> None:
        translations = Translations()
        translations.load(
            {"items": {"one": "{count} item", "other": "{count} items"}}, language="en"
        )
        assert translations.get("items", count=1) == "1 item"
        assert translations.get("items", count=3) == "3 items"

    def test_zero_form_is_honoured(self) -> None:
        translations = Translations()
        translations.load(
            {"items": {"zero": "nothing", "one": "one", "other": "many"}}, language="en"
        )
        assert translations.get("items", count=0) == "nothing"

    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (1, "1 елемент"),
            (2, "2 елементи"),
            (4, "4 елементи"),
            (5, "5 елементів"),
            (11, "11 елементів"),
            (21, "21 елемент"),
            (22, "22 елементи"),
            (25, "25 елементів"),
            (112, "112 елементів"),
        ],
    )
    def test_ukrainian_rules(self, count: int, expected: str) -> None:
        """Slavic plurals need few/many, not just one/other."""
        translations = Translations()
        translations.load(
            {
                "items": {
                    "one": "{count} елемент",
                    "few": "{count} елементи",
                    "many": "{count} елементів",
                }
            },
            language="uk",
        )
        translations.use("uk")
        assert translations.get("items", count=count) == expected

    def test_count_is_available_without_being_passed(self) -> None:
        translations = Translations()
        translations.load({"n": {"one": "{count}", "other": "{count}"}}, language="en")
        assert translations.get("n", count=7) == "7"


class TestFiles:
    def test_load_file_infers_the_language(self, tmp_path: Path) -> None:
        path = tmp_path / "uk.json"
        path.write_text(json.dumps({"hi": "Привіт"}), encoding="utf-8")
        translations = Translations()
        assert translations.load_file(path) == "uk"
        translations.use("uk")
        assert translations.get("hi") == "Привіт"

    def test_load_dir_reads_every_catalogue(self, tmp_path: Path) -> None:
        (tmp_path / "en.json").write_text('{"hi": "Hi"}', encoding="utf-8")
        (tmp_path / "de.json").write_text('{"hi": "Hallo"}', encoding="utf-8")
        translations = Translations()
        assert set(translations.load_dir(tmp_path)) == {"en", "de"}

    def test_missing_file_reports_clearly(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            Translations().load_file(tmp_path / "nope.json")

    def test_invalid_json_reports_clearly(self, tmp_path: Path) -> None:
        path = tmp_path / "en.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            Translations().load_file(path)

    def test_non_object_json_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "en.json"
        path.write_text("[1, 2]", encoding="utf-8")
        with pytest.raises(ValueError, match="object of messages"):
            Translations().load_file(path)

    def test_empty_language_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="language"):
            Translations().load({"a": "b"}, language="")


class TestDeviceLanguage:
    def test_bridge_value_wins(self) -> None:
        set_bridge(StubBridge(verbose=False, language="uk-UA"))
        try:
            assert device_language() == "uk-ua"
        finally:
            reset_bridge()

    def test_environment_is_used_off_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_bridge(StubBridge(verbose=False))
        monkeypatch.delenv("PYMOBILE_LANGUAGE", raising=False)
        monkeypatch.setenv("LANG", "fr_FR.UTF-8")
        try:
            assert device_language() == "fr-fr"
        finally:
            reset_bridge()

    def test_language_list_takes_the_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_bridge(StubBridge(verbose=False))
        monkeypatch.delenv("PYMOBILE_LANGUAGE", raising=False)
        monkeypatch.setenv("LANGUAGE", "de_DE:en_US")
        try:
            assert device_language() == "de-de"
        finally:
            reset_bridge()

    def test_default_when_nothing_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        set_bridge(StubBridge(verbose=False))
        for name in ("PYMOBILE_LANGUAGE", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            monkeypatch.delenv(name, raising=False)
        try:
            assert device_language(default="pl") == "pl"
        finally:
            reset_bridge()


class TestGettextInterop:
    def test_mo_catalogue_can_back_the_translations(self, tmp_path: Path) -> None:
        """Projects with an existing xgettext workflow keep it."""
        import struct

        messages = {"": "Content-Type: text/plain; charset=UTF-8\n", "cat": "кіт"}
        keys = sorted(messages)
        offsets = []
        payload = b""
        for key in keys:
            key_bytes = key.encode()
            value_bytes = messages[key].encode()
            offsets.append((len(payload), len(key_bytes)))
            payload += key_bytes + b"\x00"
            offsets.append((len(payload), len(value_bytes)))
            payload += value_bytes + b"\x00"

        count = len(keys)
        start = 7 * 4 + 16 * count
        header = struct.pack("<7I", 0x950412DE, 0, count, 7 * 4, 7 * 4 + 8 * count, 0, 0)
        tables = b""
        for index in range(count):
            key_offset, key_length = offsets[index * 2]
            tables += struct.pack("<II", key_length, start + key_offset)
        for index in range(count):
            value_offset, value_length = offsets[index * 2 + 1]
            tables += struct.pack("<II", value_length, start + value_offset)

        locale_dir = tmp_path / "locale" / "uk" / "LC_MESSAGES"
        locale_dir.mkdir(parents=True)
        (locale_dir / "app.mo").write_bytes(header + tables + payload)

        translations = Translations()
        translations.use("uk")
        translations.install_gettext("app", tmp_path / "locale")
        assert translations.get("cat") == "кіт"

    def test_missing_catalogue_is_not_fatal(self, tmp_path: Path) -> None:
        translations = Translations()
        translations.install_gettext("nope", tmp_path)  # must not raise


class TestLanguageSwitchingRedraws:
    """Changing language must update what is already on screen."""

    def test_visible_screen_is_rebuilt(self) -> None:
        from pymobile import App, Column, Label, Screen
        from pymobile.core.bridge import StubBridge
        from pymobile.core.i18n import translations as shared

        shared.clear()
        shared.load({"hi": "Hello"}, language="en")
        shared.load({"hi": "Привіт"}, language="uk")
        shared.use("en")

        class Home(Screen):
            def build(self):  # type: ignore[no-untyped-def]
                from pymobile import t

                return Column(Label(t("hi")))

        bridge = StubBridge(verbose=False)
        app = App("Demo", bridge=bridge)
        app.run(Home())

        def shown() -> str:
            tree = bridge.last_tree
            assert tree is not None
            return str(tree["children"][0]["props"]["text"])

        try:
            assert shown() == "Hello"
            shared.use("uk")
            assert shown() == "Привіт"
        finally:
            app.stop()
            shared.clear()

    def test_switching_to_the_same_language_does_nothing(self) -> None:
        translations = Translations()
        seen: list[str] = []
        translations.subscribe(seen.append)
        translations.use("en")  # already the default
        assert seen == []

    def test_listeners_can_unsubscribe(self) -> None:
        translations = Translations()
        seen: list[str] = []
        cancel = translations.subscribe(seen.append)
        translations.use("uk")
        cancel()
        translations.use("de")
        assert seen == ["uk"]

    def test_a_failing_listener_does_not_break_switching(self) -> None:
        translations = Translations()

        def boom(_language: str) -> None:
            raise RuntimeError("bad listener")

        seen: list[str] = []
        translations.subscribe(boom)
        translations.subscribe(seen.append)
        translations.use("uk")
        assert seen == ["uk"]

    def test_stopped_app_stops_listening(self) -> None:
        """A stopped app must not be resurrected by a language change."""
        from pymobile import App, Column, Label, Screen
        from pymobile.core.bridge import StubBridge
        from pymobile.core.i18n import translations as shared

        shared.clear()
        shared.load({"hi": "Hello"}, language="en")
        shared.use("en")

        class Home(Screen):
            def build(self):  # type: ignore[no-untyped-def]
                return Column(Label("x"))

        bridge = StubBridge(verbose=False)
        app = App("Demo", bridge=bridge)
        app.run(Home())
        app.stop()
        before = len(bridge.calls_named("render"))
        shared.use("uk")
        assert len(bridge.calls_named("render")) == before
        shared.clear()


class TestFullDateFormatting:
    def test_ukrainian_full_date_includes_weekday_and_genitive_month(self) -> None:
        assert format_date(date(2026, 10, 10), "full", language="uk") == (
            "субота, 10 жовтня 2026 р."
        )


class TestFlatKeys:
    """A dot is part of the key: lookups never descend into nested JSON."""

    def _catalogue(self) -> Translations:
        return Translations(default_language="en")

    def test_nested_catalogue_is_reported_at_load_time(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Silence is the bug: the screen renders bare keys, nothing raises."""
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        catalogue = self._catalogue()
        catalogue.load({"stats": {"balance": "Баланс", "income": "Дохід"}}, language="uk")
        err = capsys.readouterr().err
        assert "nested objects" in err
        assert "'stats'" in err
        assert "flatten=True" in err

    def test_the_same_catalogue_is_reported_once(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        catalogue = self._catalogue()
        catalogue.load({"stats": {"balance": "Баланс"}}, language="uk")
        catalogue.load({"stats": {"balance": "Баланс"}}, language="uk")
        assert capsys.readouterr().err.count("nested objects") == 1

    def test_plural_forms_are_not_mistaken_for_a_namespace(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        catalogue = Translations(default_language="uk")
        catalogue.load({"items": {"one": "1", "few": "3", "many": "5"}}, language="uk")
        assert "nested objects" not in capsys.readouterr().err
        catalogue.use("uk")
        assert catalogue.get("items", count=3) == "3"

    def test_a_dotted_miss_says_the_dot_is_part_of_the_key(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        catalogue = self._catalogue()
        catalogue.load({"stats": {"balance": "Баланс"}}, language="en")
        assert catalogue.get("stats.balance") == "stats.balance"
        err = capsys.readouterr().err
        assert "missing translation" in err
        assert "a dot is part of the key" in err

    def test_flatten_makes_dotted_lookups_work(self) -> None:
        catalogue = self._catalogue()
        catalogue.load({"stats": {"balance": "Баланс"}}, language="uk", flatten=True)
        catalogue.use("uk")
        assert catalogue.get("stats.balance") == "Баланс"

    def test_flatten_keeps_nested_plural_forms(self) -> None:
        from pymobile.core.i18n import flatten_catalogue

        flat = flatten_catalogue(
            {
                "items": {"one": "1", "other": "{count}"},
                "stats": {"balance": "Баланс"},
            }
        )
        assert flat == {"items": {"one": "1", "other": "{count}"}, "stats.balance": "Баланс"}

    def test_flatten_reaches_deep_sections_and_plural_forms(self) -> None:
        from pymobile.core.i18n import flatten_catalogue

        assert flatten_catalogue({"a": {"b": {"c": "d"}}}) == {"a.b.c": "d"}
        assert flatten_catalogue({"a": {"b": {"one": "1", "other": "n"}}}) == {
            "a.b": {"one": "1", "other": "n"}
        }

    def test_nested_file_loads_flat_with_flatten(self, tmp_path: Path) -> None:
        path = tmp_path / "uk.json"
        path.write_text('{"stats": {"balance": "Баланс"}}', encoding="utf-8")
        catalogue = self._catalogue()
        assert catalogue.load_file(path, flatten=True) == "uk"
        catalogue.use("uk")
        assert catalogue.get("stats.balance") == "Баланс"

    def test_nested_file_without_flatten_keeps_the_old_behaviour(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pymobile.logging import configure

        configure("warning")
        capsys.readouterr()
        path = tmp_path / "uk.json"
        path.write_text('{"stats": {"balance": "Баланс"}}', encoding="utf-8")
        catalogue = self._catalogue()
        catalogue.load_file(path)
        catalogue.use("uk")
        assert catalogue.get("stats.balance") == "stats.balance"
        assert "nested objects" in capsys.readouterr().err

    def test_flatten_is_exported_from_the_package(self) -> None:
        import pymobile

        assert pymobile.flatten_catalogue({"a": {"b": "c"}}) == {"a.b": "c"}
