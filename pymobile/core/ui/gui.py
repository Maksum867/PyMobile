"""An interactive desktop preview built on Tkinter.

The ASCII preview answers "does the layout look right"; this one answers
"does the app *work*". It renders the serialised widget tree into real Tk
widgets, so buttons can be clicked, switches flipped and text typed — and the
Python callbacks behind them run exactly as they would on a phone, including
navigation between screens.

Like the Android renderer, a new tree is patched into the existing widgets
whenever the structure is unchanged. Rebuilding wholesale would destroy the
focused Entry on every keystroke, which is precisely the bug the device
renderer already learned to avoid.

Tkinter needs no third-party Python package; Avatar photos optionally use Pillow.
This is a developer
tool: no attempt is made to look like Material Design, only to be faithful
about structure, state and interaction.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...log import get_logger
from .contract import text_value
from .extras_preview import build_extra_gui, patch_extra_gui
from .flow import flow_lines, line_offset
from .registry import preview_renderer

if TYPE_CHECKING:  # pragma: no cover - typing only
    import tkinter as tk

    from ..app import App

__all__ = ["GuiPreview", "run_gui", "tkinter_available", "skeleton"]

_log = get_logger("ui.gui")

#: Roughly a phone in portrait, in points.
_WINDOW = (400, 760)

_PALETTE = {
    "bg": "#FFFFFF",
    "chrome": "#F1F3F5",
    "text": "#212121",
    "muted": "#757575",
    "line": "#DFE3E8",
    "surface": "#F5F5F5",
    "primary": "#3F51B5",
    "on_primary": "#FFFFFF",
    "error": "#C62828",
}

#: Containers that simply stack their children vertically in the preview.
_VERTICAL = ("Column", "Container", "SafeArea", "Expanded", "Flexible")


def tkinter_available() -> bool:
    """Whether Tkinter can be imported (a display is a separate question)."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def place_flow(frame: Any, cells: list[Any], gap: int, run_gap: int, align: str = "start") -> None:
    """Lay ``cells`` out inside ``frame`` in lines, like the device's ``FlowLayout``.

    Tk has no flow geometry manager, so the cells are ``place()``d by hand from
    their requested sizes and the frame is given the height that results. It runs
    whenever the frame is resized; the height is only written when it changed,
    which is what stops the resulting ``<Configure>`` from calling it forever.
    Any failure is logged and leaves the cells where they were — a preview must
    not die of a layout detail.
    """
    try:
        width = int(frame.winfo_width())
        if width <= 1 or not cells:  # not mapped yet, or nothing to place
            return
        widths = [int(cell.winfo_reqwidth()) for cell in cells]
        heights = [int(cell.winfo_reqheight()) for cell in cells]
        y = 0
        for line in flow_lines(widths, width, gap):
            used = sum(widths[i] for i in line) + gap * (len(line) - 1)
            x = int(line_offset(align, width, used))
            for index in line:
                cells[index].place(x=x, y=y)
                x += widths[index] + gap
            y += max(heights[i] for i in line) + run_gap
        total = max(1, y - run_gap)
        if int(frame.cget("height")) != total:
            frame.configure(height=total)
    except Exception:  # pragma: no cover - defensive, see the docstring
        _log.exception("could not lay out a Wrap")


def skeleton(node: dict[str, Any]) -> tuple[Any, ...]:
    """Structural fingerprint, excluding values supported by in-place patches.

    Two trees with the same skeleton can be patched into one another; anything
    else needs a rebuild. Styles and rebuild-required properties are included.
    """
    props = node.get("props", {})
    # Props that change which Tk widgets exist (a refresh button, swipe
    # buttons) are structure too.
    extra: tuple[Any, ...] = tuple(
        bool(props.get(name))
        for name in ("refreshable", "refreshing", "swipe_left", "swipe_right")
        if name in props
    )
    if node.get("type") == "PageView":
        # Another page is another subtree: it cannot be patched into this one.
        extra += (props.get("value"), props.get("item_count"), props.get("loop"))
    mutable = {
        "Label": {"text"},
        "Button": {"text"},
        "TextInput": {"value", "revision"},
        "SearchBar": {"value"},
        "Switch": {"checked"},
        "Checkbox": {"checked", "text"},
        "Slider": {"value", "minimum", "maximum", "step"},
        "Stepper": {"value"},
        "Dropdown": {"value"},
        "SegmentedButtons": {"value"},
        "BottomNavigation": {"value"},
        "Chip": {"selected", "text"},
        "RadioButton": {"selected", "text"},
        "ProgressBar": {"value", "maximum", "indeterminate"},
        "ProgressText": {"value", "maximum", "text", "fraction", "percent"},
        "Badge": {"text"},
        "Avatar": {"text", "source"},
        "RatingBar": {"rating"},
        "ListTile": {"title", "subtitle", "trailing"},
        "Icon": {"drawing"},
        "IconButton": {"drawing"},
        "Canvas": {"drawing", "background"},
        "Chart": {"drawing", "background", "values"},
        "AutoComplete": {"value", "revision", "options"},
        "RangeSlider": {"low", "high"},
    }.get(node.get("type", ""), set())
    extra += (
        repr(node.get("style", {})),
        tuple((key, repr(value)) for key, value in sorted(props.items()) if key not in mutable),
    )
    return (
        node.get("type"),
        node.get("id"),
        node.get("visible", True),
        extra,
        tuple(skeleton(child) for child in node.get("children", ())),
    )


class GuiPreview:
    """Renders an :class:`~pymobile.core.app.App` into a Tk window.

    The app keeps driving itself: this class only supplies a bridge that
    turns rendered trees into Tk widgets and feeds interactions back in.
    """

    def __init__(
        self, app: App, *, title: str = "", scale: float = 1.0, assets: str | Path | None = None
    ) -> None:
        import tkinter as tk

        self.app = app
        self.assets = Path(assets) if assets is not None else Path.cwd()
        self._tk = tk
        self.root = tk.Tk()
        self.root.title(title or app.name)
        width, height = (int(value * scale) for value in _WINDOW)
        self.root.geometry(f"{width}x{height}")
        self.root.configure(bg=self.palette["chrome"])
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        header = tk.Frame(self.root, bg=self.palette["chrome"])
        header.pack(fill="x")
        self._header = header
        self._title = tk.Label(
            header,
            text=app.name,
            bg=self.palette["chrome"],
            fg=self.palette["text"],
            font=("TkDefaultFont", 10, "bold"),
            anchor="w",
            padx=12,
            pady=6,
        )
        self._title.pack(side="left")
        self._back = tk.Button(header, text="← back", command=self.back, relief="flat")
        # Packed and forgotten dynamically, so it only shows when it works.

        self._canvas = tk.Frame(self.root, bg=self.palette["bg"])
        self._canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._status = tk.Label(
            self.root,
            text="",
            bg=self.palette["chrome"],
            fg=self.palette["muted"],
            font=("TkDefaultFont", 9),
            anchor="w",
            padx=12,
            pady=4,
        )
        self._status.pack(fill="x")

        # The snackbar strip sits between the screen and the status line and
        # is only packed while App.snackbar() has one on screen.
        self._snack = tk.Frame(self.root, bg="#323232")
        self._snack_text = tk.Label(
            self._snack, text="", bg="#323232", fg="#FFFFFF", anchor="w", padx=12, pady=8
        )
        self._snack_text.pack(side="left", fill="x", expand=True)
        self._snack_close = tk.Button(
            self._snack,
            text="✕",
            relief="flat",
            bg="#323232",
            fg="#BDBDBD",
            bd=0,
            highlightthickness=0,
            activebackground="#424242",
            command=lambda: self._dispatch("__snackbar__", "dismiss", self._snack_token),
        )
        self._snack_close.pack(side="right")
        self._snack_action = tk.Button(
            self._snack,
            text="",
            relief="flat",
            bg="#323232",
            fg="#8C9EFF",
            bd=0,
            highlightthickness=0,
            activebackground="#424242",
            command=lambda: self._dispatch("__snackbar__", "press", self._snack_token),
        )
        self._snack_token = ""

        #: Leaf widgets by widget id, used to patch instead of rebuild.
        self._widgets: dict[str, Any] = {}
        self._variables: dict[str, Any] = {}
        self._extra_states: dict[str, Any] = {}
        self._skeleton: tuple[Any, ...] | None = None
        self._patching = False
        self._closing = False
        self._revisions: dict[str, int] = {}
        self._sync_theme()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        """Tear the window down."""
        self._close()

    def _close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self.app.stop()
        finally:
            self.root.quit()
            self.root.destroy()

    def run(self) -> None:
        """Enter the Tk main loop until the window is closed."""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:  # pragma: no cover - interactive
            self._close()

    # -- rendering ---------------------------------------------------------
    def render(self, tree: dict[str, Any]) -> None:
        """Show a freshly rendered tree.

        Timers fire on scheduler threads, and Tk may only be touched from the
        thread owning the window, so the work is handed to the event loop.
        """
        if self._closing:
            return
        self.root.after(0, lambda: self._apply(tree))

    def _apply(self, tree: dict[str, Any]) -> None:
        if self._closing:
            return
        if self._sync_theme():
            self._skeleton = None
        depth = self.app.navigator.depth
        self._title.configure(text=str(tree.get("screen") or self.app.name))
        if depth > 1:
            self._back.pack(side="right", padx=8)
        else:
            self._back.pack_forget()

        self._sync_snackbar(tree.get("snackbar"))
        shape = skeleton(tree)
        try:
            if shape == self._skeleton:
                self._patch(tree)
            else:
                self._rebuild(tree)
                self._skeleton = shape
        except Exception as error:  # pragma: no cover - defensive
            _log.exception("preview render failed")
            self._status.configure(text=f"⚠ render error: {error}")

    def _rebuild(self, tree: dict[str, Any]) -> None:
        """Throw the view away and build it again from scratch."""
        for child in self._canvas.winfo_children():
            child.destroy()
        self._widgets.clear()
        self._variables.clear()
        self._extra_states.clear()
        self._revisions.clear()
        self._patching = True
        try:
            self._build(self._canvas, tree)
        finally:
            self._patching = False

    def _patch(self, node: dict[str, Any]) -> None:
        """Update the values of existing widgets in place."""
        self._patching = True
        try:
            self._patch_node(node)
        finally:
            self._patching = False

    def _patch_node(self, node: dict[str, Any]) -> None:
        widget = self._widgets.get(str(node.get("id", "")))
        props = node.get("props", {})
        kind = node.get("type")

        if kind in (
            "Icon",
            "IconButton",
            "AutoComplete",
            "RangeSlider",
            "PageView",
            "Chart",
            "Canvas",
        ):
            patch_extra_gui(self, node)
        elif kind == "RatingBar" and widget is not None:
            self._paint_stars(widget, props.get("rating", 0))
        elif widget is not None:
            state = "normal" if node.get("enabled", True) else "disabled"
            if kind in (
                "Button",
                "TextInput",
                "SearchBar",
                "Switch",
                "Checkbox",
                "Chip",
                "RadioButton",
                "Dropdown",
                "Slider",
            ):
                widget.configure(state=state)
            if kind in ("Label", "Button"):
                widget.configure(text=text_value(props.get("text", "")))
            elif kind in ("ProgressBar", "ProgressText"):
                bar = widget if kind == "ProgressBar" else widget.bar
                mode = "indeterminate" if props.get("indeterminate") else "determinate"
                changed = str(bar.cget("mode")) != mode
                if changed:
                    bar.stop()
                bar.configure(
                    maximum=float(props.get("maximum", 100)) or 100,
                    value=float(props.get("value", 0)),
                    mode=mode,
                )
                if changed and mode == "indeterminate":
                    bar.start(60)
                if kind == "ProgressText":
                    widget.label.configure(text=text_value(props.get("text")))
            elif kind == "Slider":
                widget.configure(
                    from_=float(props.get("minimum", 0)),
                    to=float(props.get("maximum", 100)),
                    resolution=float(props.get("step") or -1),
                )
                variable = self._variables[node["id"]]
                if variable.get() != float(props.get("value", 0)):
                    variable.set(float(props.get("value", 0)))
            elif kind == "Dropdown":
                values = props.get("options") or []
                labels = props.get("labels") or values
                value = props.get("value", "")
                index = values.index(value) if value in values else -1
                self._variables[node["id"]].set(
                    labels[index] if 0 <= index < len(labels) else value
                )
            elif kind == "Stepper":
                widget.label.configure(text=text_value(props.get("value", 0)))
                for button in widget.buttons:
                    button.configure(state=state)
            elif kind in ("SegmentedButtons", "BottomNavigation"):
                self._selection(widget, props, state)
            elif kind == "Chip":
                widget.configure(text=text_value(props.get("text")))
                self._chip_colour(widget, node)
            elif kind == "RadioButton":
                widget.configure(text=text_value(props.get("text")))
                self._variables[node["id"]].set(int(bool(props.get("selected"))))
            elif kind in ("Avatar", "Badge"):
                self._paint_identity(widget, node)
            elif kind == "ListTile":
                widget.configure(text=self._tile_text(props), state=state)
            elif kind in ("Switch", "Checkbox"):
                variable = self._variables.get(node["id"])
                checked = bool(props.get("checked"))
                if variable is not None and variable.get() != checked:
                    variable.set(checked)
                widget.configure(
                    text=text_value(props.get("text", ""))
                    if kind == "Checkbox"
                    else ("on" if checked else "off")
                )
            elif kind in ("TextInput", "SearchBar"):
                variable = self._variables.get(node["id"])
                value = text_value(props.get("value", ""))
                # Never write into the field being typed in: it would move the
                # caret to the end after every keystroke.
                focused = self.root.focus_get() is widget
                revision = int(props.get("revision", 0))
                forced = kind == "SearchBar" or revision != self._revisions.get(node["id"], 0)
                if variable is not None and (not focused or forced) and variable.get() != value:
                    caret = widget.index("insert")
                    variable.set(value)
                    widget.icursor(min(caret, len(value)))
                self._revisions[node["id"]] = revision

        for child in node.get("children", ()):
            self._patch_node(child)

    @staticmethod
    def _paint_stars(frame: Any, rating: Any) -> None:
        """Show ``rating`` as filled and empty stars (rounded to whole stars)."""
        try:
            filled = round(float(rating or 0))
        except (TypeError, ValueError):
            filled = 0
        for index, star in enumerate(frame.stars):
            star.configure(text="★" if index < filled else "☆")

    def _sync_snackbar(self, data: dict[str, Any] | None) -> None:
        if not data:
            self._snack.pack_forget()
            return
        self._snack_token = str(data.get("token", ""))
        self._snack_text.configure(text=str(data.get("message", "")))
        action = str(data.get("action", ""))
        if action:
            self._snack_action.configure(text=action)
            # Packed after ✕ with side="right", so it sits just left of it.
            self._snack_action.pack(side="right")
        else:
            self._snack_action.pack_forget()
        if not self._snack.winfo_ismapped():
            self._snack.pack(fill="x", before=self._status, padx=8, pady=(0, 6))

    def toast(self, message: str) -> None:
        """Show a transient message in the status strip."""
        if self._closing:
            return
        self.root.after(0, lambda: self._status.configure(text=f"🔔 {message}"))
        self.root.after(2500, self._clear_toast)

    def _clear_toast(self) -> None:
        if not self._closing:
            self._status.configure(text="")

    # -- widget construction ----------------------------------------------
    def _build(self, parent: tk.Misc, node: dict[str, Any]) -> None:
        """Create the Tk widget for ``node`` and pack it into ``parent``."""
        if not node.get("visible", True):
            return

        tk = self._tk
        kind = node.get("type", "Label")
        props = node.get("props", {})
        style = node.get("style", {})
        enabled = node.get("enabled", True)
        widget_id = str(node.get("id", ""))
        background = self._colour(style.get("background"), str(parent.cget("bg")))
        pad = 2

        custom = preview_renderer(kind, "gui")
        if custom is not None:
            try:
                custom(self, parent, node, background)
            except Exception as error:  # pragma: no cover - depends on user code
                _log.warning(
                    "the gui preview renderer for %r failed: %s: %s",
                    kind,
                    type(error).__name__,
                    error,
                )
                tk.Label(
                    parent,
                    text=f"<{kind}: {type(error).__name__}>",
                    fg=self.palette["muted"],
                    bg=background,
                ).pack(anchor="w")
            return

        if kind == "ScrollView":
            if props.get("horizontal"):
                # No horizontal scrolling in the preview; a plain row keeps the
                # arrangement honest, which is what the picture is for.
                self._build_row(parent, node, background)
            else:
                self._build_scroll(parent, node, background)
            return

        if kind in (
            "Icon",
            "IconButton",
            "AutoComplete",
            "RangeSlider",
            "PageView",
            "Chart",
            "Canvas",
        ):
            build_extra_gui(self, parent, node, background)
            return

        if kind == "Stack":
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            cells = []
            for child in node.get("children", ()):
                if not child.get("visible", True):
                    continue
                cell = tk.Frame(frame, bg=background)
                self._build(cell, child)
                cell.place(x=0, y=0, relwidth=1)
                cells.append(cell)

            def size_stack(_event: object = None) -> None:
                height = style.get("height")
                height = (
                    int(height)
                    if isinstance(height, (int, float))
                    else max((cell.winfo_reqheight() for cell in cells), default=1)
                )
                if int(frame.cget("height")) != height:
                    frame.configure(height=height)

            frame.bind("<Configure>", size_stack)
            for cell in cells:
                cell.bind("<Configure>", size_stack)
            frame.after_idle(size_stack)
            self._widgets[widget_id] = frame
            self._bind_tap(frame, node, widget_id)
            return

        if kind in _VERTICAL:
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="both" if kind != "Column" else "x", expand=kind != "Column", pady=pad)
            self._build_children(frame, node, background)
            # C-3: a tappable container is clickable in the Tk window too, so
            # the preview shows the same interaction as the phone.
            self._bind_tap(frame, node, widget_id)
            return

        if kind == "Row":
            self._build_row(parent, node, background)
            return

        if kind == "Grid":
            self._build_grid(parent, node, props, background)
            return

        if kind == "Wrap":
            self._build_wrap(parent, node, props, background)
            return

        if kind == "Divider":
            thickness = max(1, int(props.get("thickness", 1)))
            colour = self.palette["line"]
            tk.Frame(parent, height=thickness, bg=colour).pack(fill="x", pady=6)
            return

        if kind == "Label":
            label = tk.Label(
                parent,
                text=text_value(props.get("text", "")),
                bg=background,
                fg=self._colour(style.get("color"), self.palette["text"]),
                font=self._font(style),
                justify="left",
                anchor="w",
                wraplength=340,
            )
            label.pack(fill="x", pady=pad)
            self._widgets[widget_id] = label
            return

        if kind == "Button":
            button = tk.Button(
                parent,
                text=text_value(props.get("text", "")),
                font=self._font(style),
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(widget_id, "press", ""),
            )
            self._button_colour(button, node)
            button.pack(fill="x", pady=pad)
            self._widgets[widget_id] = button
            return

        if kind == "TextInput":
            variable = tk.StringVar(master=self.root, value=text_value(props.get("value", "")))
            entry = tk.Entry(
                parent,
                textvariable=variable,
                font=self._font(style),
                show="•" if props.get("password") else "",
                state="normal" if enabled else "disabled",
            )
            entry.pack(fill="x", pady=pad)
            variable.trace_add(
                "write",
                lambda *_: self._dispatch(widget_id, "change", variable.get()),
            )
            self._widgets[widget_id] = entry
            self._variables[widget_id] = variable
            self._revisions[widget_id] = int(props.get("revision", 0))
            if props.get("placeholder") and not props.get("value"):
                self._show_placeholder(entry, str(props["placeholder"]))
            return

        if kind == "Switch":
            variable = tk.BooleanVar(master=self.root, value=bool(props.get("checked")))
            toggle = tk.Checkbutton(
                parent,
                variable=variable,
                text="on" if variable.get() else "off",
                bg=background,
                anchor="w",
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(
                    widget_id, "toggle", "true" if variable.get() else "false"
                ),
            )
            toggle.pack(fill="x", pady=pad)
            self._widgets[widget_id] = toggle
            self._variables[widget_id] = variable
            return

        if kind == "ProgressBar":
            from tkinter import ttk

            maximum = float(props.get("maximum", 100)) or 100.0
            bar = ttk.Progressbar(
                parent,
                style="PyMobile.Horizontal.TProgressbar",
                maximum=maximum,
                value=float(props.get("value", 0)),
                mode="indeterminate" if props.get("indeterminate") else "determinate",
            )
            bar.pack(fill="x", pady=pad)
            if props.get("indeterminate"):
                bar.start(60)
            self._widgets[widget_id] = bar
            return

        if kind == "Image":
            image = tk.Label(
                parent,
                text=f"🖼 {props.get('source', '')}",
                fg=self.palette["muted"],
                bg=background,
                anchor="w",
            )
            image.pack(fill="x", pady=pad)
            self._widgets[widget_id] = image
            return

        if kind == "Spacer":
            tk.Frame(parent, height=int(props.get("size", 8)), bg=background).pack()
            return

        if kind in ("Checkbox",):
            variable = tk.BooleanVar(master=self.root, value=bool(props.get("checked")))
            box = tk.Checkbutton(
                parent,
                variable=variable,
                text=text_value(props.get("text", "")),
                bg=background,
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(
                    widget_id, "toggle", "true" if variable.get() else "false"
                ),
            )
            box.pack(fill="x", pady=pad)
            self._widgets[widget_id] = box
            self._variables[widget_id] = variable
            return

        if kind == "Slider":
            variable = tk.DoubleVar(master=self.root, value=float(props.get("value", 0)))
            scale = tk.Scale(
                parent,
                from_=float(props.get("minimum", 0)),
                to=float(props.get("maximum", 100)),
                orient="horizontal",
                resolution=float(props.get("step") or -1),
                state="normal" if enabled else "disabled",
                variable=variable,
                command=lambda v: self._dispatch(widget_id, "change", str(v)),
            )
            scale.pack(fill="x", pady=pad)
            self._widgets[widget_id] = scale
            self._variables[widget_id] = variable
            return

        if kind == "RatingBar":
            maximum = max(1, int(props.get("maximum", 5) or 5))
            frame = tk.Frame(parent, bg=background)
            frame.pack(anchor="w", pady=pad)
            colour = self._colour(style.get("color"), self.palette["text"])
            stars: list[Any] = []
            for number in range(1, maximum + 1):
                star = tk.Label(frame, bg=background, fg=colour, font=("TkDefaultFont", 18))
                star.pack(side="left")
                if node.get("enabled", True):
                    star.bind(
                        "<Button-1>",
                        lambda _event, n=number: self._dispatch(widget_id, "change", str(float(n))),
                    )
                stars.append(star)
            frame.stars = stars  # type: ignore[attr-defined]
            self._paint_stars(frame, props.get("rating", 0))
            self._widgets[widget_id] = frame
            return

        if kind == "Dropdown":
            # П-11: ``options`` is the list of values; ``labels`` is the
            # parallel list of visible strings (falls back to the value).
            option_values = [str(o) for o in (props.get("options") or [])]
            option_labels = [str(lbl) for lbl in (props.get("labels") or option_values)]
            while len(option_labels) < len(option_values):
                option_labels.append(option_values[len(option_labels)])
            default_val = option_values[0] if option_values else ""
            current_value = text_value(props.get("value", default_val))
            try:
                current_label = option_labels[option_values.index(current_value)]
            except ValueError:
                current_label = current_value
            variable = tk.StringVar(master=self.root, value=current_label)

            def _pick(
                lbl: str,
                v: list[str] = option_values,
                labels: list[str] = option_labels,
            ) -> None:
                try:
                    self._dispatch(widget_id, "change", v[labels.index(lbl)])
                except ValueError:
                    self._dispatch(widget_id, "change", lbl)

            menu = tk.OptionMenu(parent, variable, *(option_labels or [""]), command=_pick)
            menu.configure(state="normal" if enabled else "disabled")
            menu.pack(fill="x", pady=pad)
            self._widgets[widget_id] = menu
            self._variables[widget_id] = variable
            return

        if kind == "Chip":
            button = tk.Button(
                parent,
                text=text_value(props.get("text")),
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(widget_id, "press", ""),
            )
            self._chip_colour(button, node)
            button.pack(anchor="w", pady=pad)
            self._widgets[widget_id] = button
            return

        if kind in ("Badge", "Avatar"):
            canvas = tk.Canvas(parent, bg=background, highlightthickness=0)
            canvas.pack(anchor="w", pady=pad)
            self._widgets[widget_id] = canvas
            self._paint_identity(canvas, node)
            return

        if kind == "Stepper":
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            minus = tk.Button(
                frame,
                text="-",
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(widget_id, "decrement", ""),
            )
            minus.pack(side="left")
            label = tk.Label(frame, text=text_value(props.get("value", 0)), bg=background)
            label.pack(side="left", padx=8)
            plus = tk.Button(
                frame,
                text="+",
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(widget_id, "increment", ""),
            )
            plus.pack(side="left")
            frame.label = label
            frame.buttons = [minus, plus]
            self._widgets[widget_id] = frame
            return

        if kind == "SearchBar":
            variable = tk.StringVar(master=self.root, value=text_value(props.get("value", "")))
            entry = tk.Entry(
                parent, textvariable=variable, state="normal" if enabled else "disabled"
            )
            entry.pack(fill="x", pady=pad)
            variable.trace_add(
                "write", lambda *_: self._dispatch(widget_id, "change", variable.get())
            )
            self._widgets[widget_id] = entry
            self._variables[widget_id] = variable
            return

        if kind == "RadioButton":
            variable = tk.IntVar(master=self.root, value=int(bool(props.get("selected"))))
            radio = tk.Radiobutton(
                parent,
                text=text_value(props.get("text")),
                variable=variable,
                value=1,
                bg=background,
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(widget_id, "press", ""),
            )
            radio.pack(anchor="w")
            self._variables[widget_id] = variable
            self._widgets[widget_id] = radio
            return

        if kind == "RadioGroup":
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            self._build_children(frame, node, background)
            return

        if kind == "SegmentedButtons":
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            option_values = [str(o) for o in (props.get("options") or [])]
            option_labels = [str(lbl) for lbl in (props.get("labels") or option_values)]
            while len(option_labels) < len(option_values):
                option_labels.append(option_values[len(option_labels)])
            selected = text_value(props.get("value", ""))
            wrap = bool(props.get("wrap"))
            buttons = []
            for val, lbl in zip(option_values, option_labels, strict=False):
                button = tk.Button(
                    frame,
                    text=lbl,
                    relief="sunken" if val == selected else "raised",
                    command=lambda v=val: self._dispatch(widget_id, "change", v),
                )
                buttons.append(button)
                if not wrap:  # one row; Tk never squeezes a word into a column of letters
                    button.pack(side="left", expand=True, fill="x")
            frame.buttons = buttons
            self._selection(frame, props, "normal" if enabled else "disabled")
            self._widgets[widget_id] = frame
            if wrap:
                self._flow(frame, buttons, 0, 0, "start")
            return

        if kind == "ProgressText":
            from tkinter import ttk

            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            bar = ttk.Progressbar(
                frame,
                maximum=float(props.get("maximum", 100)) or 100,
                value=float(props.get("value", 0)),
                style="PyMobile.Horizontal.TProgressbar",
            )
            bar.pack(fill="x")
            label = tk.Label(frame, text=text_value(props.get("text")), bg=background, anchor="w")
            label.pack(fill="x")
            frame.bar = bar
            frame.label = label
            self._widgets[widget_id] = frame
            return

        if kind == "Link":
            tk.Button(
                parent,
                text=text_value(props.get("text", "")),
                fg="#3F51B5",
                relief="flat",
                command=lambda: self._dispatch(widget_id, "press", ""),
            ).pack(anchor="w")
            return

        if kind == "DataTable":
            headers = " | ".join(str(h) for h in (props.get("headers") or []))
            tk.Label(
                parent,
                text=headers,
                bg=background,
                anchor="w",
                font=("TkDefaultFont", 9, "bold"),
            ).pack(fill="x")
            for row in props.get("rows") or []:
                tk.Label(
                    parent,
                    text=" | ".join(str(c) for c in row),
                    bg=background,
                    anchor="w",
                ).pack(fill="x")
            return

        if kind in ("List", "ListTile"):
            if kind == "List":
                frame = tk.Frame(parent, bg=background)
                frame.pack(fill="both", expand=True, pady=pad)
                if props.get("refreshable"):
                    # A mouse cannot pull: the gesture becomes a button.
                    refreshing = bool(props.get("refreshing"))
                    tk.Button(
                        frame,
                        text="⟳ Refreshing…" if refreshing else "↻ Pull to refresh",
                        state="disabled" if refreshing else "normal",
                        command=lambda: self._dispatch(widget_id, "refresh", ""),
                    ).pack(fill="x", pady=pad)
                self._build_children(frame, node, background)
                if props.get("has_more"):
                    loaded = text_value(props.get("loaded", len(node.get("children", ()))))
                    tk.Button(
                        frame,
                        text=f"Load more ({loaded} of {props.get('item_count', '?')})",
                        command=lambda: self._dispatch(widget_id, "load_more", loaded),
                    ).pack(fill="x", pady=pad)
                return
            holder = parent
            if props.get("swipe_left") or props.get("swipe_right"):
                holder = tk.Frame(parent, bg=background)
                holder.pack(fill="x", pady=pad)
                for direction, arrow in (("left", "⟵"), ("right", "⟶")):
                    if props.get(f"swipe_{direction}"):
                        tk.Button(
                            holder,
                            text=arrow,
                            bg=self._colour(props.get(f"swipe_{direction}_color"), "#757575"),
                            fg="#FFFFFF",
                            command=lambda d=direction: self._dispatch(widget_id, "swipe", d),
                        ).pack(side="right")
            tile = tk.Button(
                holder,
                text=self._tile_text(props),
                state="normal" if enabled else "disabled",
                command=lambda: self._dispatch(widget_id, "press", ""),
                anchor="w",
            )
            self._widgets[widget_id] = tile
            if holder is parent:
                tile.pack(fill="x", pady=pad)
            else:
                tile.pack(side="left", fill="x", expand=True)
            if props.get("long_pressable"):
                # Right click stands in for touch-and-hold in the Tk window.
                tile.bind(
                    "<Button-3>",
                    lambda _event, wid=widget_id: self._dispatch(wid, "long_press", ""),
                )
            return

        if kind == "BottomNavigation":
            bar = tk.Frame(parent, bg=background)
            bar.pack(fill="x", pady=pad)
            option_values = [str(o) for o in (props.get("options") or [])]
            option_labels = [str(lbl) for lbl in (props.get("labels") or option_values)]
            while len(option_labels) < len(option_values):
                option_labels.append(option_values[len(option_labels)])
            buttons = []
            for val, lbl in zip(option_values, option_labels, strict=False):
                selected = val == props.get("value")
                button = tk.Button(
                    bar,
                    text=lbl,
                    relief="sunken" if selected else "raised",
                    command=lambda v=val: self._dispatch(widget_id, "change", v),
                )
                button.pack(side="left", expand=True, fill="x")
                buttons.append(button)
            bar.buttons = buttons
            self._selection(bar, props, "normal" if enabled else "disabled")
            self._widgets[widget_id] = bar
            return

        if kind == "Dialog":
            box = tk.LabelFrame(
                parent, text=text_value(props.get("title", "")), bg=background, padx=8, pady=8
            )
            box.pack(fill="x", pady=pad, side="bottom" if props.get("sheet") else "top")
            self._build_children(box, node, background)
            return

        if kind in ("DatePicker", "TimePicker"):
            entry = tk.Entry(parent, width=14)
            entry.insert(0, text_value(props.get("value", "")))
            entry.bind(
                "<Return>",
                lambda _event, entry=entry: self._dispatch(widget_id, "change", entry.get()),
            )
            entry.pack(anchor="w", pady=pad)
            return

        tk.Label(parent, text=f"<{kind}>", fg=self.palette["muted"], bg=background).pack(anchor="w")

    def _build_children(self, frame: tk.Misc, node: dict[str, Any], background: str) -> None:
        """Stack children vertically, honouring the container's spacing."""
        spacing = int(node.get("props", {}).get("spacing", 0))
        for index, child in enumerate(node.get("children", ())):
            if index and spacing:
                self._tk.Frame(frame, height=spacing, bg=background).pack()
            self._build(frame, child)

    def _bind_tap(self, widget: Any, node: dict[str, Any], widget_id: str) -> None:
        """Make a container with ``on_press``/``on_long_press`` tappable (C-3).

        Tk reports a press on the innermost widget, so the binding walks up to
        the container that has a handler; a Button inside a tappable Card keeps
        handling its own taps instead of firing both.
        """
        props = node.get("props", {})
        handlers = {
            "press": bool(props.get("on_press")),
            "long_press": bool(props.get("on_long_press")),
        }
        if not any(handlers.values()):
            return
        if not node.get("enabled", True):
            return
        widget.configure(cursor="hand2")
        if handlers["press"]:
            widget.bind(
                "<Button-1>",
                lambda _event, target=widget_id: self._dispatch(target, "press", ""),
            )
        if handlers["long_press"]:
            # The Tk window has no touch, so a right click stands in for a hold,
            # which is what the browser preview does as well.
            widget.bind(
                "<Button-3>",
                lambda _event, target=widget_id: self._dispatch(target, "long_press", ""),
            )

    def _build_row(self, parent: tk.Misc, node: dict[str, Any], background: str) -> None:
        """A horizontal band; Expanded/Flexible children share the leftovers."""
        tk = self._tk
        frame = tk.Frame(parent, bg=background)
        frame.pack(fill="x", pady=2)
        spacing = int(node.get("props", {}).get("spacing", 0))
        for index, child in enumerate(node.get("children", ())):
            flex = self._flex_of(child)
            cell = tk.Frame(frame, bg=background)
            cell.pack(
                side="left",
                expand=bool(flex),
                fill="both" if flex else "none",
                padx=(spacing if index else 0, 0),
            )
            self._build(cell, child)
        self._bind_tap(frame, node, node.get("id", ""))

    def _build_grid(
        self, parent: tk.Misc, node: dict[str, Any], props: dict[str, Any], background: str
    ) -> None:
        """Lay a Grid out with Tk's own grid geometry manager."""
        tk = self._tk
        columns = max(1, int(props.get("columns", 2)))
        row_spacing = int(props.get("row_spacing", 0))
        column_spacing = int(props.get("column_spacing", 0))

        frame = tk.Frame(parent, bg=background)
        frame.pack(fill="x", pady=2)
        for column in range(columns):
            # uniform= is what makes the columns genuinely equal in width.
            frame.columnconfigure(column, weight=1, uniform="pymobile-grid")

        for index, child in enumerate(node.get("children", ())):
            row, column = divmod(index, columns)
            cell = tk.Frame(frame, bg=background)
            cell.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else column_spacing, 0),
                pady=(0 if row == 0 else row_spacing, 0),
            )
            self._build(cell, child)

    def _flow(self, frame: tk.Misc, cells: list[Any], gap: int, run_gap: int, align: str) -> None:
        """Keep ``cells`` laid out as a flow inside ``frame`` whenever it is resized."""

        def layout(_event: object = None) -> None:
            place_flow(frame, cells, gap, run_gap, align)

        frame.bind("<Configure>", layout)
        frame.after_idle(layout)

    def _build_wrap(
        self, parent: tk.Misc, node: dict[str, Any], props: dict[str, Any], background: str
    ) -> None:
        """A Wrap: children side by side, a new line when the row is full."""
        tk = self._tk
        gap = int(props.get("spacing", 0) or 0)
        run_value = props.get("run_spacing")
        run_gap = gap if run_value is None else int(run_value or 0)
        frame = tk.Frame(parent, bg=background, height=1)
        frame.pack(fill="x", pady=2)
        cells = []
        for child in node.get("children", ()):
            if not child.get("visible", True):
                continue  # hidden takes no place, not even a gap
            cell = tk.Frame(frame, bg=background)
            self._build(cell, child)
            cells.append(cell)
        self._flow(frame, cells, gap, run_gap, text_value(props.get("align") or "start"))

    def _build_scroll(self, parent: tk.Misc, node: dict[str, Any], background: str) -> None:
        """A scrollable region: a Canvas holding a Frame, plus a scrollbar."""
        tk = self._tk
        holder = tk.Frame(parent, bg=background)
        holder.pack(fill="both", expand=True)

        canvas = tk.Canvas(holder, bg=background, highlightthickness=0)
        scrollbar = tk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=background)

        window = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Keep the inner frame as wide as the viewport, or children packed with
        # fill="x" would collapse to their natural width.
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

        self._build_children(content, node, background)

    def _show_placeholder(self, entry: tk.Entry, text: str) -> None:
        """Grey placeholder text that disappears on focus."""
        entry.insert(0, text)
        entry.configure(fg=self.palette["muted"])

        def on_focus_in(_event: object) -> None:
            if entry.get() == text:
                entry.delete(0, "end")
                entry.configure(fg=self.palette["text"])

        entry.bind("<FocusIn>", on_focus_in)

    # -- helpers -----------------------------------------------------------
    def _flex_of(self, node: dict[str, Any]) -> int:
        """The flex share a row child claims, or 0 for a fixed-size child."""
        if node.get("type") in ("Expanded", "Flexible"):
            return int(node.get("props", {}).get("flex", 1))
        weight = node.get("style", {}).get("weight")
        return int(weight) if weight else 0

    def _font(self, style: dict[str, Any]) -> tuple[str, int, str]:
        size = int(style.get("font_size", 11))
        weight = "bold" if style.get("bold") else "normal"
        return ("TkDefaultFont", max(7, size), weight)

    def _colour(self, value: Any, fallback: str) -> str:
        """Translate a framework colour into one Tk understands."""
        if not isinstance(value, str) or not value.startswith("#"):
            return fallback
        # Tk knows #RGB and #RRGGBB but not Android's #AARRGGBB.
        return f"#{value[3:]}" if len(value) == 9 else value

    def _background(self, style: dict[str, Any]) -> str:
        return self._colour(style.get("background"), self.palette["bg"])

    @property
    def palette(self) -> dict[str, str]:
        return getattr(self, "_palette", _PALETTE)

    def _sync_theme(self) -> bool:
        from tkinter import ttk

        from .theme import Theme

        theme = getattr(self.app, "theme", Theme.light())
        names = {
            "bg": "BACKGROUND",
            "chrome": "SURFACE",
            "surface": "SURFACE",
            "text": "TEXT",
            "muted": "TEXT_MUTED",
            "line": "TEXT_MUTED",
            "primary": "PRIMARY",
            "on_primary": "ON_PRIMARY",
            "error": "ERROR",
        }
        palette = {
            key: self._colour(theme[name], _PALETTE.get(key, "#FFFFFF"))
            for key, name in names.items()
        }
        if getattr(self, "_palette", None) == palette:
            return False
        self._palette = palette
        self.root.configure(bg=palette["chrome"])
        self._header.configure(bg=palette["chrome"])
        self._canvas.configure(bg=palette["bg"])
        self._title.configure(bg=palette["chrome"], fg=palette["text"])
        self._status.configure(bg=palette["chrome"], fg=palette["muted"])
        self._back.configure(
            bg=palette["chrome"],
            fg=palette["text"],
            activebackground=palette["surface"],
            activeforeground=palette["text"],
        )
        for resource, colour in {
            "Background": palette["bg"],
            "Foreground": palette["text"],
            "activeBackground": palette["surface"],
            "activeForeground": palette["text"],
            "selectColor": palette["surface"],
            "insertBackground": palette["text"],
            "selectBackground": palette["primary"],
            "selectForeground": palette["on_primary"],
            "disabledForeground": palette["muted"],
            "troughColor": palette["surface"],
        }.items():
            self.root.option_add("*" + resource, colour)
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "PyMobile.Horizontal.TProgressbar",
            background=palette["primary"],
            troughcolor=palette["surface"],
            bordercolor=palette["surface"],
        )
        return True

    def _button_colour(self, button: Any, node: dict[str, Any]) -> None:
        from .theme import _readable_on

        variant = node.get("props", {}).get("variant", "primary")
        bg, fg = self.palette["primary"], self.palette["on_primary"]
        if variant in ("text", "outlined", "tonal"):
            bg, fg = self.palette["surface"], self.palette["primary"]
        elif variant == "danger":
            bg = self.palette["error"]
            fg = _readable_on(bg)
        style = node.get("style", {})
        bg, fg = self._colour(style.get("background"), bg), self._colour(style.get("color"), fg)
        button.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg)

    def _chip_colour(self, button: Any, node: dict[str, Any]) -> None:
        selected = node.get("props", {}).get("selected", False)
        style = node.get("style", {})
        bg = self._colour(
            style.get("background"), self.palette["primary" if selected else "surface"]
        )
        fg = self._colour(style.get("color"), self.palette["on_primary" if selected else "text"])
        button.configure(
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            relief="sunken" if selected else "raised",
        )

    def _selection(self, frame: Any, props: dict[str, Any], state: str) -> None:
        for button, value in zip(frame.buttons, props.get("options", ()), strict=False):
            selected = value == props.get("value")
            bg = self.palette["primary" if selected else "surface"]
            fg = self.palette["on_primary" if selected else "text"]
            button.configure(
                relief="sunken" if selected else "raised",
                bg=bg,
                fg=fg,
                activebackground=bg,
                activeforeground=fg,
                state=state,
            )

    @staticmethod
    def _tile_text(props: dict[str, Any]) -> str:
        title = text_value(props.get("title"))
        subtitle, trailing = text_value(props.get("subtitle")), text_value(props.get("trailing"))
        return (
            title + ("    " + trailing if trailing else "") + ("\n" + subtitle if subtitle else "")
        )

    def _paint_identity(self, canvas: Any, node: dict[str, Any]) -> None:
        from tkinter.font import Font

        props, style = node.get("props", {}), node.get("style", {})
        signature = (repr(props), repr(style))
        if getattr(canvas, "identity_signature", None) == signature:
            return
        canvas.identity_signature = signature
        avatar = node.get("type") == "Avatar"
        text = text_value(props.get("text"))
        if avatar:
            text = text[:2].upper()
        font = self._font(style)
        measured = Font(root=self.root, font=font)
        natural_w = props.get("size", 48) if avatar else measured.measure(text) + 12
        natural_h = props.get("size", 48) if avatar else measured.metrics("linespace") + 4
        width, height = style.get("width"), style.get("height")
        width = max(1, int(width if isinstance(width, (int, float)) else natural_w))
        height = max(1, int(height if isinstance(height, (int, float)) else natural_h))
        bg = self._colour(style.get("background", props.get("background")), self.palette["primary"])
        fg = self._colour(style.get("color", props.get("color")), self.palette["on_primary"])
        canvas.configure(width=width, height=height)
        canvas.delete("all")
        canvas.photo = None
        if avatar:
            canvas.create_oval(0, 0, width, height, fill=bg, outline="")
        else:
            radius = min(width, height) / 2
            canvas.create_oval(0, 0, radius * 2, height, fill=bg, outline="")
            canvas.create_oval(width - radius * 2, 0, width, height, fill=bg, outline="")
            canvas.create_rectangle(radius, 0, width - radius, height, fill=bg, outline="")
        canvas.create_text(width / 2, height / 2, text=text, fill=fg, font=font)
        source = text_value(props.get("source")) if avatar else ""
        if source:
            try:
                from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageTk

                path = Path(source)
                if not path.is_absolute():
                    path = self.assets / path
                with Image.open(path) as original:
                    photo = ImageOps.fit(original.convert("RGBA"), (width, height))
                mask = Image.new("L", (width, height))
                ImageDraw.Draw(mask).ellipse((0, 0, width - 1, height - 1), fill=255)
                photo.putalpha(ImageChops.multiply(mask, photo.getchannel("A")))
                canvas.photo = ImageTk.PhotoImage(photo, master=self.root)
                # Photo replaces initials, but retains the coloured circular fallback.
                canvas.delete("all")
                canvas.create_oval(0, 0, width, height, fill=bg, outline="")
                canvas.create_image(0, 0, anchor="nw", image=canvas.photo)
            except (ImportError, OSError, ValueError):
                pass  # Pillow is optional; missing/unsupported images keep initials.

    @property
    def tk(self) -> Any:
        """The tkinter module, for project-registered preview renderers.

        A custom widget's ``gui`` renderer receives the preview, the parent
        frame and the node; importing tkinter itself would tie application
        code (which also runs on the phone) to a desktop-only dependency.
        """
        return self._tk

    def dispatch(self, widget_id: str, kind: str, value: str) -> None:
        """Send an event from a project-registered preview renderer.

        The counterpart of ``on_press=…`` on a built-in widget: a custom Tk
        control calls this to run the application handler exactly as a tap
        on the phone would.
        """
        self._dispatch(widget_id, kind, value)

    def _dispatch(self, widget_id: str, kind: str, value: str) -> None:
        """Feed an interaction back into the application."""
        # Writing a value during a patch must not look like user input.
        if self._closing or self._patching or not widget_id:
            return
        try:
            self.app.handle_ui_event(widget_id, kind, value)
        except Exception as error:  # pragma: no cover - user callback failed
            _log.exception("handler for %s failed", widget_id)
            self._status.configure(text=f"⚠ {type(error).__name__}: {error}")

    def back(self) -> None:
        """Emulate the hardware back button."""
        self._dispatch("system", "back", "")


def run_gui(
    app: App,
    *,
    title: str = "",
    scale: float = 1.0,
    on_ready: Callable[[GuiPreview], None] | None = None,
) -> GuiPreview:
    """Show ``app`` in an interactive window and block until it is closed."""
    preview = GuiPreview(app, title=title, scale=scale)
    if on_ready is not None:
        on_ready(preview)
    preview.run()
    return preview
