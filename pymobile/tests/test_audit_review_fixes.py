"""Regression tests for the 12 findings of the code audit.

Each class covers one finding; the reproduction script (``audit_repro.py``)
walks the same ground from the outside, these run inside the suite.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import pytest

from pymobile import (
    AlertDialog,
    App,
    Button,
    Checkbox,
    Column,
    ConfirmDialog,
    DataTable,
    HttpSecurityPolicy,
    Label,
    List,
    ListTile,
    Screen,
    Storage,
    Switch,
    TextInput,
    Validator,
    Widget,
)
from pymobile.compiler.pipeline import build_apk
from pymobile.core.bridge import StubBridge
from pymobile.core.config import DEFAULT_EXCLUDE, ProjectConfig
from pymobile.core.ui.contract import text_value
from pymobile.errors import NetworkError, PyMobileError


def _configure_logging() -> None:
    from pymobile.logging import configure

    configure("warning")


# --------------------------------------------------------------------------
# 1. exclude adds to the built-in patterns
# --------------------------------------------------------------------------
class TestExcludeDefaults:
    def _project(self, tmp_path: Path, toml: str) -> ProjectConfig:
        from pymobile.core.config import load_config

        (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
        (tmp_path / "build").mkdir(exist_ok=True)
        (tmp_path / "build" / "secret.txt").write_text("token", encoding="utf-8")
        (tmp_path / "pymobile.toml").write_text(toml, encoding="utf-8")
        return load_config(tmp_path)

    def test_a_user_pattern_does_not_drop_the_defaults(self, tmp_path: Path) -> None:
        config = self._project(tmp_path, 'name = "Demo"\nexclude = ["docs/**"]\n')
        assert "docs/**" in config.exclude
        assert set(DEFAULT_EXCLUDE) <= set(config.exclude)

    def test_the_output_directory_is_not_packaged(self, tmp_path: Path) -> None:
        """The README example used to ship build/ (and the previous APK) inside."""
        result = build_apk(self._project(tmp_path, 'name = "Demo"\nexclude = ["tests/**"]\n'))
        with zipfile.ZipFile(result.apk) as archive:
            assert not [name for name in archive.namelist() if "build/" in name]
        assert not any("output directory" in warning for warning in result.warnings)

    def test_exclude_only_replaces_the_defaults(self, tmp_path: Path) -> None:
        config = self._project(
            tmp_path, 'name = "Demo"\nexclude = ["docs/**"]\nexclude_only = true\n'
        )
        assert config.exclude == ["docs/**"]
        result = build_apk(config)
        with zipfile.ZipFile(result.apk) as archive:
            leaked = [name for name in archive.namelist() if "build/" in name]
        assert leaked  # the opt-out is honoured …
        assert any("output directory" in warning for warning in result.warnings)  # … but loud

    def test_an_uncovered_output_dir_is_reported(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
        result = build_apk(ProjectConfig(root=tmp_path, output_dir="out"))
        assert any("output directory" in warning for warning in result.warnings)

    def test_a_direct_exclude_is_merged_too(self, tmp_path: Path) -> None:
        config = ProjectConfig(root=tmp_path, exclude=["docs/**"])
        assert set(DEFAULT_EXCLUDE) <= set(config.exclude)


# --------------------------------------------------------------------------
# 2. None in a text prop means empty, in every renderer
# --------------------------------------------------------------------------
class TestNoneTextProps:
    def test_text_input_none_is_empty(self) -> None:
        field = TextInput(None)
        assert field.value == ""
        assert field.to_dict()["props"]["value"] == ""

    def test_text_input_assigned_none_is_empty(self) -> None:
        field = TextInput("value")
        field.value = None  # type: ignore[assignment]
        assert field.value == ""

    def test_list_tile_none_is_empty(self) -> None:
        tile = ListTile(None, subtitle=None, trailing=None)
        assert tile.to_dict()["props"]["title"] == ""
        tile.subtitle = None  # type: ignore[assignment]
        assert tile.subtitle == ""

    def test_the_browser_preview_does_not_print_none(self) -> None:
        from pymobile.core.ui.web import render_html

        html = render_html(TextInput(None).to_dict()) + render_html(ListTile(None).to_dict())
        assert "None" not in html

    def test_text_value_is_the_shared_rule(self) -> None:
        assert text_value(None) == ""
        assert text_value(0) == "0"
        assert text_value("x") == "x"

    @pytest.mark.parametrize("renderer", ["web.py", "gui.py", "mockup.py", "preview.py"])
    def test_every_renderer_uses_the_helper(self, renderer: str) -> None:
        """No renderer may go back to str(props.get(…)) — that printed "None"."""
        root = Path(__file__).resolve().parents[1]
        source = (root / "core/ui" / renderer).read_text(encoding="utf-8")
        assert "str(props.get(" not in source
        assert "text_value(props.get(" in source


# --------------------------------------------------------------------------
# 3. checked is a bool everywhere
# --------------------------------------------------------------------------
class TestCheckedCoercion:
    def test_switch_coerces_a_json_string(self) -> None:
        switch = Switch()
        switch.checked = "false"  # type: ignore[assignment]
        node = switch.to_dict()
        assert node["props"]["checked"] is False
        assert not bool(node["props"]["checked"])  # the preview agrees with the phone

    def test_switch_coerces_truthy_values(self) -> None:
        switch = Switch()
        switch.checked = 1  # type: ignore[assignment]
        assert switch.checked is True

    def test_checkbox_coerces_too(self) -> None:
        box = Checkbox(checked=False)
        box.checked = "no"  # type: ignore[assignment]
        assert box.to_dict()["props"]["checked"] is False

    def test_the_constructor_coerces_as_well(self) -> None:
        assert Switch(checked="false").checked is False  # type: ignore[arg-type]
        assert Checkbox(checked="on").checked is True  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 4. a misspelled kwarg on a built-in widget is named
# --------------------------------------------------------------------------
class TestUnknownProps:
    def test_a_typo_is_reported(self, capsys: pytest.CaptureFixture[str]) -> None:
        _configure_logging()
        capsys.readouterr()
        Label("hi", colr="red").to_dict()
        err = capsys.readouterr().err
        assert "Label has no prop 'colr'" in err

    def test_the_report_happens_once(self, capsys: pytest.CaptureFixture[str]) -> None:
        _configure_logging()
        label = Label("unique-for-this-test", labl="x")
        capsys.readouterr()
        label.to_dict()
        label.to_dict()
        assert capsys.readouterr().err.count("has no prop") == 1

    def test_a_close_match_is_suggested(self, capsys: pytest.CaptureFixture[str]) -> None:
        _configure_logging()
        capsys.readouterr()
        Column(cross_alig="center").to_dict()
        assert "Did you mean 'cross_align'?" in capsys.readouterr().err

    def test_custom_widget_types_keep_extension_props(self) -> None:
        class BarChart(Widget):
            type_name = "BarChart"

            def props(self):  # type: ignore[no-untyped-def]
                return {**super().props(), "series": [1, 2]}

        chart = BarChart(legend="bottom")  # an extension prop: its own renderer's job
        assert chart.to_dict()["props"]["legend"] == "bottom"

    def test_a_callable_typo_names_the_argument(self) -> None:
        with pytest.raises(TypeError, match="'on_pres' is not a prop of Button"):
            Button("b", on_pres=lambda: None)  # type: ignore[call-arg]

    def test_extension_props_still_work_when_set_on_purpose(self) -> None:
        widget = Label("hi")
        widget.set_prop("hint", "x")
        assert widget.to_dict()["props"]["hint"] == "x"


# --------------------------------------------------------------------------
# 5. run() after stop() says so
# --------------------------------------------------------------------------
class Home(Screen):
    """A concrete screen: the base ``Screen`` cannot be built."""

    def build(self) -> Widget:
        return Column(Label("home"))


class TestRestartAfterStop:
    def _app(self) -> App:
        return App("Demo", bridge=StubBridge(verbose=False))

    def test_run_after_stop_raises(self) -> None:
        app = self._app()
        app.run(Home())
        app.stop()
        with pytest.raises(PyMobileError, match="already been stopped") as caught:
            app.run(Home())
        assert "create a new App" in (caught.value.hint or "")

    def test_run_twice_without_stop_is_unchanged(self) -> None:
        app = self._app()
        app.run(Home())
        app.run(Home())  # desktop run() returns immediately; this stays allowed
        assert app.navigator.depth == 1
        app.stop()

    def test_a_fresh_app_after_stop_works(self) -> None:
        first = self._app()
        first.run(Home())
        first.stop()
        second = self._app()
        second.run(Home())
        assert second.run_job(lambda: 1) is not None
        second.stop()


# --------------------------------------------------------------------------
# 6. allowed_hosts understands a port
# --------------------------------------------------------------------------
class TestSecurityPolicyPorts:
    def test_host_with_port_matches(self) -> None:
        policy = HttpSecurityPolicy(allowed_hosts=["api.example.com:8443"])
        policy.validate("https://api.example.com:8443/v1")

    def test_default_port_matches_a_bare_url(self) -> None:
        HttpSecurityPolicy(allowed_hosts=["api.example.com:443"]).validate(
            "https://api.example.com/v1"
        )

    def test_a_bare_host_still_matches_any_port(self) -> None:
        HttpSecurityPolicy(allowed_hosts=["api.example.com"]).validate(
            "https://api.example.com:8443/v1"
        )

    def test_another_port_is_still_blocked(self) -> None:
        policy = HttpSecurityPolicy(allowed_hosts=["api.example.com:8443"])
        with pytest.raises(NetworkError, match="blocked"):
            policy.validate("https://api.example.com:9443/v1")

    def test_the_error_lists_what_is_allowed(self) -> None:
        policy = HttpSecurityPolicy(allowed_hosts=["api.example.com"])
        with pytest.raises(NetworkError) as caught:
            policy.validate("https://other.example.com/")
        assert "api.example.com" in (caught.value.hint or "")

    def test_an_empty_allow_list_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            HttpSecurityPolicy(allowed_hosts=["  "])

    def test_require_https_is_unaffected(self) -> None:
        policy = HttpSecurityPolicy(require_https=True, allowed_hosts=["api.example.com:80"])
        with pytest.raises(NetworkError, match="Insecure HTTP"):
            policy.validate("http://api.example.com/")


# --------------------------------------------------------------------------
# 7. Validator accepts a single rule
# --------------------------------------------------------------------------
class TestValidatorShapes:
    def test_a_bare_string_is_one_rule(self) -> None:
        assert Validator({"email": "email"}).validate({"email": "nope"}) == {
            "email": "must be a valid email address"
        }

    def test_a_single_mapping_is_one_rule(self) -> None:
        errors = Validator({"age": {"between": [1, 120]}}).validate({"age": "300"})
        assert "age" in errors

    def test_a_single_callable_is_one_rule(self) -> None:
        def always_fails(_value: object) -> str:
            return "nope"

        assert Validator({"x": always_fails}).validate({"x": "1"}) == {"x": "nope"}

    def test_the_list_form_is_unchanged(self) -> None:
        rules = Validator({"email": ["required", "email"]})
        assert rules.validate({}) == {"email": "is required"}

    def test_normalize_expands_every_shape(self) -> None:
        assert Validator.normalize({"email": "email"}) == {"email": ["email"]}
        assert Validator.normalize({"age": [{"between": [1, 2]}], "n": "integer"}) == {
            "age": [{"between": [1, 2]}],
            "n": ["integer"],
        }

    def test_a_non_rule_type_is_rejected_clearly(self) -> None:
        with pytest.raises(ValueError, match="must be a rule or a sequence of rules"):
            Validator({"email": 42})  # type: ignore[dict-item]


# --------------------------------------------------------------------------
# 8. toggle() respects enabled
# --------------------------------------------------------------------------
class TestToggleRespectsEnabled:
    def test_a_disabled_switch_does_not_flip(self) -> None:
        switch = Switch(enabled=False)
        assert switch.toggle() is False
        assert switch.checked is False

    def test_a_disabled_switch_does_not_notify(self) -> None:
        seen: list[bool] = []
        switch = Switch(enabled=False, on_toggle=seen.append)
        switch.toggle()
        assert seen == []

    def test_an_enabled_switch_still_flips(self) -> None:
        switch = Switch()
        assert switch.toggle() is True

    def test_a_disabled_checkbox_does_not_flip(self) -> None:
        assert Checkbox(checked=False, enabled=False).toggle() is False


# --------------------------------------------------------------------------
# 9. List.item_count is validated by the setter too
# --------------------------------------------------------------------------
class TestListItemCount:
    def _list(self) -> List:
        return List(10, builder=lambda index: ListTile(f"row {index}"))

    def test_a_negative_value_raises(self) -> None:
        lst = self._list()
        with pytest.raises(ValueError, match="must not be negative"):
            lst.item_count = -5

    def test_a_non_int_raises(self) -> None:
        lst = self._list()
        with pytest.raises(TypeError, match="must be an int"):
            lst.item_count = 2.5  # type: ignore[assignment]

    def test_shrinking_drops_the_extra_rows_on_refresh(self) -> None:
        lst = self._list()
        lst.item_count = 3
        lst.refresh()
        assert len(lst.children) == 3
        assert lst.props()["loaded"] == 3
        assert lst.props()["has_more"] is False

    def test_growing_builds_more_on_refresh(self) -> None:
        lst = List(2, builder=lambda index: ListTile(f"row {index}"))
        lst.item_count = 5
        lst.refresh()
        assert len(lst.children) == 5

    def test_the_constructor_message_is_unchanged(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            List(-1)


# --------------------------------------------------------------------------
# 10. DataTable fits rows the same way everywhere
# --------------------------------------------------------------------------
class TestDataTableRows:
    def test_the_constructor_rejects_extra_cells(self) -> None:
        with pytest.raises(ValueError, match=r"has 3 cells"):
            DataTable(["a", "b"], [["1", "2", "3"]])

    def test_add_row_rejects_extra_cells(self) -> None:
        with pytest.raises(ValueError):
            DataTable(["a", "b"]).add_row(["1", "2", "3"])

    def test_short_rows_are_padded(self) -> None:
        assert DataTable(["a", "b", "c"], [["1"]]).rows == [["1", "", ""]]

    def test_none_cells_become_empty_strings(self) -> None:
        assert DataTable(["a", "b"], [[None, 1]]).rows == [["", "1"]]

    def test_values_are_stringified(self) -> None:
        assert DataTable(["n", "m", "k"], [[1, 2.5, True]]).rows == [["1", "2.5", "True"]]


# --------------------------------------------------------------------------
# 11. Storage: numbers stay numbers, a missing key is named
# --------------------------------------------------------------------------
class TestStorageEdges:
    def _store(self) -> Storage:
        return Storage(Path(tempfile.mkdtemp()) / "store.json")

    def test_increment_parses_a_numeric_string(self) -> None:
        store = self._store()
        store.set("n", "5")
        assert store.increment("n") == 6.0
        assert store.get("n") == 6.0

    def test_increment_of_a_non_number_starts_from_zero(self) -> None:
        store = self._store()
        store.set("n", {"a": 1})
        assert store.increment("n") == 1.0

    def test_increment_of_a_missing_key_starts_from_zero(self) -> None:
        assert self._store().increment("fresh") == 1.0

    def test_increment_ignores_bools(self) -> None:
        store = self._store()
        store.set("flag", True)
        assert store.increment("flag") == 1.0

    def test_update_of_a_missing_key_demands_a_default(self) -> None:
        store = self._store()
        with pytest.raises(PyMobileError, match="does not exist") as caught:
            store.update("cart", lambda value: [*value, "x"])
        assert "default=" in (caught.value.hint or "")

    def test_update_with_an_explicit_default_still_works(self) -> None:
        store = self._store()
        assert store.update("cart", lambda value: [*value, "x"], default=[]) == ["x"]

    def test_update_with_an_explicit_none_default_is_kept(self) -> None:
        store = self._store()
        assert store.update("k", lambda value: value, default=None) is None


# --------------------------------------------------------------------------
# 12. Dialog outcome helpers are idempotent
# --------------------------------------------------------------------------
class TestDialogHelpers:
    def test_confirm_twice_fires_once(self) -> None:
        seen: list[str] = []
        dialog = ConfirmDialog("Delete?", on_confirm=lambda: seen.append("confirm"))
        dialog.open()
        dialog.confirm()
        dialog.confirm()
        dialog.cancel()  # the dialog is already resolved
        assert seen == ["confirm"]

    def test_reopening_allows_another_outcome(self) -> None:
        seen: list[str] = []
        dialog = ConfirmDialog("Delete?", on_cancel=lambda: seen.append("cancel"))
        dialog.open()
        dialog.cancel()
        dialog.open()
        dialog.cancel()
        assert seen == ["cancel", "cancel"]

    def test_alert_dialog_has_a_public_acknowledge(self) -> None:
        seen: list[str] = []
        alert = AlertDialog("Hi", on_acknowledge=lambda: seen.append("ack"))
        alert.open()
        alert.acknowledge()
        alert.acknowledge()
        assert seen == ["ack"]
        assert alert.shown is False

    def test_dismiss_still_acknowledges(self) -> None:
        seen: list[str] = []
        alert = AlertDialog("Hi", on_acknowledge=lambda: seen.append("ack"))
        alert.open()
        alert.dismiss()
        assert seen == ["ack"]

    def test_confirm_closes_the_dialog_first(self) -> None:
        shown: list[bool] = []
        dialog = ConfirmDialog("?")

        def handler() -> None:
            shown.append(dialog.shown)

        dialog.on_confirm = handler
        dialog.open()
        dialog.confirm()
        assert shown == [False]
