"""Final audit block: actual Pillow/Tk rendering and native source contracts."""

from copy import deepcopy
from inspect import signature
from pathlib import Path

import pytest
from PIL import Image as PILImage
from PIL import ImageFont

import pymobile
from pymobile import (
    App,
    Avatar,
    Badge,
    BottomNavigation,
    Button,
    Checkbox,
    Chip,
    Column,
    Dropdown,
    Label,
    ListTile,
    ProgressBar,
    ProgressText,
    RadioButton,
    SearchBar,
    SegmentedButtons,
    Slider,
    Stack,
    Stepper,
    StubBridge,
    Style,
    TextInput,
    Theme,
)
from pymobile.core.ui._mockup_colors import _palette
from pymobile.core.ui._mockup_fonts import _Fonts
from pymobile.core.ui._mockup_layout import _Layout
from pymobile.core.ui.gui import GuiPreview, skeleton
from pymobile.core.ui.mockup import render_mockup
from pymobile.core.ui.web import render_html

JAVA = Path(pymobile.__file__).parent / "resources/android/java"


@pytest.mark.parametrize("size", [24, 48, 72])
def test_avatar_is_intrinsically_square_in_column(size):
    avatar = Avatar("abcdef", size=size)
    node = avatar.to_dict()
    assert node["style"]["width"] == node["style"]["height"] == size
    layout = _Layout(_Fonts(ImageFont, 1), _palette(None), None, 1)
    box = layout.layout(Column(avatar).to_dict(), 360, fill=True)
    assert box.kids[0][2].w == box.kids[0][2].h == size


def test_badge_intrinsic_width_pill_and_explicit_style():
    badge = Badge("99+", background="#FF0000")
    assert badge.to_dict()["style"]["width"] == "wrap"
    layout = _Layout(_Fonts(ImageFont, 1), _palette(None), None, 1)
    box = layout.layout(badge.to_dict(), 360, fill=True)
    assert 20 < box.w < 100
    image = render_mockup(Column(badge), width=360, height=100, scale=1, chrome=False)
    assert image.getpixel((0, 0))[:3] == (255, 255, 255)
    assert image.getpixel((6, 3))[:3] != (255, 255, 255)
    assert Badge("x", style=Style(width=120)).to_dict()["style"]["width"] == 120


@pytest.mark.parametrize("size", [24, 48, 72])
def test_png_avatar_loads_circular_photo_and_falls_back(tmp_path, size):
    PILImage.new("RGB", (60, 20), "red").save(tmp_path / "face.png")
    avatar = Avatar(text="ab", image="face.png", size=size, background="#0000FF")
    photo = render_mockup(avatar, width=200, height=100, scale=1, chrome=False, assets=tmp_path)
    assert photo.getpixel((size // 2, size // 2))[:3] == (255, 0, 0)
    assert photo.getpixel((0, 0))[:3] == (255, 255, 255)
    assert photo.getpixel((size + 1, size // 2))[:3] == (255, 255, 255)
    missing = render_mockup(
        Avatar(text="ab", image="missing.png", size=size, background="#0000FF"),
        width=200,
        height=100,
        scale=1,
        chrome=False,
        assets=tmp_path,
    )
    assert missing.getpixel((size // 2, 3))[:3] == (0, 0, 255)
    assert missing.tobytes() != photo.tobytes()


def test_web_avatar_source_is_escaped_and_failure_preserves_initials():
    html = render_html(Avatar(text="abcdef", image='photo.png?x="<', size=64).to_dict())
    assert "AB<img" in html and "object-fit:cover" in html
    assert 'onerror="this.remove()"' in html and "&quot;&lt;" in html
    assert "align-self:flex-start" in html
    assert "align-self:flex-start" in render_html(Badge("x").to_dict())


def test_android_identity_source_and_patch_contract():
    source = (JAVA / "ViewControls.java").read_text()
    assert "GradientDrawable.OVAL" in source and "pill.setCornerRadius(owner.dp(999))" in source
    assert "offsetByCodePoints" in source and "toUpperCase(java.util.Locale.ROOT)" in source
    assert "new AvatarTextView" in source and "canvas.clipPath(clip)" in source
    assert "photo.bitmap = source.isEmpty() ? null : loadLocalImage(source)" in source
    assert "image.setImageBitmap(loadLocalImage(source))" in source
    updates = (JAVA / "ViewUpdates.java").read_text()
    assert "owner.controls.applyAvatar((TextView) view, props)" in updates


@pytest.mark.parametrize("name", ["getView", "getDropDownView"])
def test_android_adapters_colour_selected_and_popup_rows(name):
    for filename in ["ViewInputs.java", "AdvancedViews.java"]:
        source = (JAVA / filename).read_text()
        assert f"View {name}(" in source and f"colour(super.{name}(" in source
        assert "setTextColor(textColor)" in source and "setBackgroundColor(surfaceColor)" in source


@pytest.mark.parametrize(
    "api",
    [
        "setButtonTintList(controlTint())",
        "setThumbTintList(controlTint())",
        "setTrackTintList(controlTint())",
        "setThumbTintList(primaryTint())",
        "setProgressTintList(primaryTint())",
        "setIndeterminateTintList(primaryTint())",
    ],
)
def test_native_dynamic_tints_are_state_aware(api):
    source = (JAVA / "ViewInputs.java").read_text()
    assert api in source
    assert "-android.R.attr.state_enabled" in source and "android.R.attr.state_checked" in source
    assert "owner.colorPrimary" in source


def test_native_picker_stepper_buttons_and_dialogs_follow_theme():
    inputs = (JAVA / "ViewInputs.java").read_text()
    controls = (JAVA / "ViewControls.java").read_text()
    assert inputs.count("owner.controls.applyButton(button, new JSONObject())") == 2
    for name in ["minus", "plus"]:
        assert f"applyButton({name}, new JSONObject())" in controls
    assert "new DatePickerDialog(pickerContext()," in inputs
    assert "new TimePickerDialog(pickerContext()," in inputs
    assert "owner.darkTheme" in inputs and "Theme_Material_Light_Dialog_Alert" in inputs


@pytest.fixture
def live(tmp_path):
    tkinter = pytest.importorskip("tkinter", reason="Tkinter is not installed")

    bridge = StubBridge(verbose=False)
    app = App("Tk audit", bridge=bridge)
    preview = None
    try:
        try:
            preview = GuiPreview(
                app,
                **({"assets": tmp_path} if "assets" in signature(GuiPreview).parameters else {}),
            )
        except tkinter.TclError as error:
            pytest.skip(f"Tk display required (run with xvfb-run): {error}")

        def show(widget=None):
            if widget is not None:
                app.run(widget)
            app.render()
            preview._apply(bridge.last_tree)
            preview.root.update_idletasks()
            preview.root.update()
            assert not str(preview._status.cget("text")).startswith("⚠")
            return deepcopy(bridge.last_tree)

        yield app, preview, show
    finally:
        if preview:
            preview.close()
        else:
            app.stop()


def by_id(preview, suffix):
    return next(
        widget
        for key, widget in preview._widgets.items()
        if key == suffix or key.endswith(":" + suffix)
    )


def node_by_id(tree, suffix):
    if tree["id"] == suffix or tree["id"].endswith(":" + suffix):
        return tree
    for child in tree.get("children", ()):
        found = node_by_id(child, suffix)
        if found:
            return found
    return None


def test_real_tk_dark_theme_and_runtime_switch(live):
    app, preview, show = live
    show(
        Column(
            Label("label", id="label"),
            Button("button", id="button"),
            Checkbox(text="check", id="check"),
        )
    )
    app.set_theme(
        Theme(
            "dark",
            {
                "BACKGROUND": "#112233",
                "SURFACE": "#223344",
                "TEXT": "#EEEEEE",
                "PRIMARY": "#CCDDEE",
            },
        )
    )
    show()
    assert preview._canvas.cget("bg") == "#112233"
    assert by_id(preview, "label").cget("fg") == "#EEEEEE"
    assert by_id(preview, "button").cget("bg") == "#CCDDEE"
    assert by_id(preview, "button").cget("fg") == "#000000"
    assert by_id(preview, "check").cget("fg") == "#EEEEEE"
    app.set_theme(Theme.light())
    show()
    assert preview._canvas.cget("bg") == "#FFFFFF"


def test_real_tk_stack_overlays_instead_of_vertical_packing(live):
    _, preview, show = live
    show(Stack(Label("back", id="back"), Label("front", id="front"), id="stack"))
    back, front = by_id(preview, "back"), by_id(preview, "front")
    assert back.winfo_rooty() == front.winfo_rooty()
    assert back.master.winfo_manager() == front.master.winfo_manager() == "place"
    stack = by_id(preview, "stack")
    assert stack.winfo_height() < back.winfo_reqheight() + front.winfo_reqheight()
    assert stack.winfo_children()[-1] == front.master


def test_real_tk_chip_clicks_radio_state_progress_and_list_tile(live):
    _, preview, show = live
    events = []
    show(
        Column(
            Chip("chip", id="chip", selected=True, on_press=lambda: events.append("chip")),
            RadioButton("radio", id="radio", selected=True),
            ProgressText(25, id="progress"),
            ListTile("Title", subtitle="Subtitle", trailing="99", id="tile"),
        )
    )
    chip = by_id(preview, "chip")
    chip.invoke()
    assert events == ["chip"] and chip.cget("relief") == "sunken"
    radio = by_id(preview, "radio")
    assert radio.getvar(radio.cget("variable")) == 1
    progress = by_id(preview, "progress")
    assert float(progress.bar.cget("value")) == 25 and "25%" in progress.label.cget("text")
    assert "Subtitle" in by_id(preview, "tile").cget("text")
    assert "99" in by_id(preview, "tile").cget("text")


FACTORIES = {
    "Checkbox": lambda: Checkbox(text="check", id="target"),
    "Dropdown": lambda: Dropdown([("a", "Alpha"), ("b", "Beta")], value="a", id="target"),
    "Slider": lambda: Slider(2, minimum=0, maximum=10, id="target"),
    "Stepper": lambda: Stepper(2, id="target"),
    "SegmentedButtons": lambda: SegmentedButtons(
        [("a", "Alpha"), ("b", "Beta")], value="a", id="target"
    ),
    "BottomNavigation": lambda: BottomNavigation(
        [("a", "Alpha"), ("b", "Beta")], value="a", id="target"
    ),
    "SearchBar": lambda: SearchBar("before", id="target"),
}


@pytest.mark.parametrize("kind", list(FACTORIES))
def test_real_tk_programmatic_values_patch_without_echo_or_focus_loss(live, kind, monkeypatch):
    app, preview, show = live
    tree = show(Column(TextInput("abcdef", id="focus"), FACTORIES[kind]()))
    target = by_id(preview, "target")
    field = by_id(preview, "focus")
    field.focus_force()
    preview.root.update()
    field.icursor(2)
    events = []
    monkeypatch.setattr(app, "handle_ui_event", lambda *args: events.append(args))
    node = node_by_id(tree, "target")
    if kind == "Checkbox":
        node["props"]["checked"] = True
    else:
        node["props"]["value"] = (
            "b"
            if kind in ("Dropdown", "SegmentedButtons", "BottomNavigation")
            else "after"
            if kind == "SearchBar"
            else 7
        )
    preview._apply(tree)
    preview.root.update_idletasks()
    preview.root.update()
    assert by_id(preview, "target") is target and by_id(preview, "focus") is field
    assert preview.root.focus_get() is field and field.index("insert") == 2
    assert events == []
    if kind in ("SegmentedButtons", "BottomNavigation"):
        assert target.buttons[1].cget("relief") == "sunken"
        assert target.buttons[0].cget("relief") == "raised"
    elif kind == "Stepper":
        assert target.label.cget("text") == "7"
    else:
        value = preview._variables[node["id"]].get()
        expected = {"Checkbox": True, "Dropdown": "Beta", "Slider": 7, "SearchBar": "after"}[kind]
        assert value == expected


@pytest.mark.parametrize("kind", list(FACTORIES))
def test_real_tk_disabled_values_apply_in_place(live, kind):
    _, preview, show = live
    tree = show(FACTORIES[kind]())
    target = by_id(preview, "target")
    tree["enabled"] = False
    preview._apply(tree)
    widgets = (
        target.buttons if kind in ("Stepper", "SegmentedButtons", "BottomNavigation") else [target]
    )
    assert by_id(preview, "target") is target
    assert all(str(widget.cget("state")) == "disabled" for widget in widgets)


def test_real_tk_structural_options_rebuild_and_preserve_new_keys(live, monkeypatch):
    app, preview, show = live
    tree = show(SegmentedButtons([("a", "A")], id="target"))
    before = by_id(preview, "target")
    changed = deepcopy(tree)
    changed["props"].update(options=["x", "y"], labels=["First", "Second"], value="y")
    assert skeleton(changed) != skeleton(tree)
    preview._apply(changed)
    after = by_id(preview, "target")
    assert after is not before and after.buttons[1].cget("text") == "Second"
    calls = []
    monkeypatch.setattr(app, "handle_ui_event", lambda *args: calls.append(args))
    after.buttons[0].invoke()
    assert calls[0][1:] == ("change", "x")


def test_real_tk_progress_mode_maximum_and_label_patch(live):
    _, preview, show = live
    tree = show(Column(ProgressBar(10, id="bar"), ProgressText(10, id="text")))
    node_by_id(tree, "bar")["props"].update(value=80, maximum=200)
    node_by_id(tree, "text")["props"].update(value=50, maximum=200, text="Quarter")
    preview._apply(tree)
    assert float(by_id(preview, "bar").cget("maximum")) == 200
    assert float(by_id(preview, "bar").cget("value")) == 80
    assert by_id(preview, "text").label.cget("text") == "Quarter"
    node_by_id(tree, "bar")["props"]["indeterminate"] = True
    preview._apply(tree)
    assert str(by_id(preview, "bar").cget("mode")) == "indeterminate"
    node_by_id(tree, "bar")["props"]["indeterminate"] = False
    preview._apply(tree)
    assert str(by_id(preview, "bar").cget("mode")) == "determinate"


def test_real_tk_avatar_photo_fallback_updates_and_badge_size(live, tmp_path):
    _, preview, show = live
    PILImage.new("RGB", (80, 20), "red").save(tmp_path / "face.png")
    tree = show(
        Column(
            Avatar(text="abcdef", image="face.png", size=48, id="avatar"), Badge("99+", id="badge")
        )
    )
    avatar = by_id(preview, "avatar")
    assert avatar.winfo_width() == avatar.winfo_height() == 48 and avatar.photo is not None
    assert avatar.tk.call(str(avatar.photo), "get", 24, 24) == (255, 0, 0)
    assert avatar.tk.call(str(avatar.photo), "transparency", "get", 0, 0) == 1
    assert by_id(preview, "badge").winfo_width() < 100
    props = node_by_id(tree, "avatar")["props"]
    props.update(source="missing.png", text="xyz")
    preview._apply(tree)
    assert by_id(preview, "avatar") is avatar and avatar.photo is None
    texts = [
        avatar.itemcget(item, "text") for item in avatar.find_all() if avatar.type(item) == "text"
    ]
    assert texts == ["XY"]
    assert "oval" in [avatar.type(item) for item in avatar.find_all()]


def test_real_tk_focused_searchbar_and_textinput_forced_updates(live):
    _, preview, show = live
    tree = show(Column(TextInput("abcdef", id="field"), SearchBar("abcdef", id="search")))
    for key in ["field", "search"]:
        widget = by_id(preview, key)
        widget.focus_force()
        preview.root.update()
        widget.icursor(2)
        props = node_by_id(tree, key)["props"]
        props["value"] = "changed"
        if key == "field":
            props["revision"] = props.get("revision", 0) + 1
        preview._apply(tree)
        assert widget.get() == "changed" and widget.index("insert") == 2
        assert preview.root.focus_get() is widget


def test_real_tk_multiselect_chip_updates_python_and_visual_state(live):
    from pymobile import MultiSelect

    _, preview, show = live
    selection = MultiSelect({"a": "Alpha", "b": "Beta"}, id="select")
    show(selection)
    chip = by_id(preview, "select:option:a")
    chip.invoke()
    show()
    assert selection.value == ("a",)
    assert by_id(preview, "select:option:a").cget("relief") == "sunken"
    by_id(preview, "select:option:a").invoke()
    show()
    assert selection.value == ()
    assert by_id(preview, "select:option:a").cget("relief") == "raised"


def test_real_tk_radio_and_list_tile_patch_in_place(live):
    _, preview, show = live
    tree = show(
        Column(RadioButton("Radio", id="radio", selected=True), ListTile("First", id="tile"))
    )
    radio, tile = by_id(preview, "radio"), by_id(preview, "tile")
    node_by_id(tree, "radio")["props"].update(selected=False, text="Changed")
    node_by_id(tree, "tile")["props"].update(title="Second", subtitle="Detail", trailing="42")
    preview._apply(tree)
    assert by_id(preview, "radio") is radio and radio.getvar(radio.cget("variable")) == 0
    assert radio.cget("text") == "Changed"
    assert by_id(preview, "tile") is tile and tile.cget("text") == "Second    42\nDetail"


def test_tk_palettes_and_tcl_variables_do_not_leak_between_windows(live):
    app, preview, show = live
    show(Checkbox(text="first", id="check"))
    other = App("Other", bridge=StubBridge(verbose=False), theme=Theme.dark())
    second = None
    try:
        second = GuiPreview(other)
        node = Checkbox(text="second", id="other-check").to_dict()
        second._build(second._canvas, node)
        variable = second._variables["other-check"]
        assert variable._tk is second.root.tk
        assert preview._canvas.cget("bg") == "#FFFFFF"
        assert second._canvas.cget("bg") == "#121212"
        assert app.theme["BACKGROUND"] == "#FFFFFF"
    finally:
        if second:
            second.close()
        else:
            other.stop()


def test_real_tk_stack_remeasures_after_in_place_text_growth(live):
    _, preview, show = live
    tree = show(Stack(Label("One", id="label"), id="stack"))
    stack = by_id(preview, "stack")
    before = stack.winfo_height()
    node_by_id(tree, "label")["props"]["text"] = "One\nTwo\nThree"
    preview._apply(tree)
    preview.root.update_idletasks()
    preview.root.update()
    assert by_id(preview, "stack") is stack and stack.winfo_height() > before * 2
