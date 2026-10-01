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

Tkinter ships with CPython, so this costs no dependency. It is a developer
tool: no attempt is made to look like Material Design, only to be faithful
about structure, state and interaction.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ...log import get_logger
from .contract import text_value
from .extras_preview import build_extra_gui, patch_extra_gui
from .flow import flow_lines, line_offset

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
}

#: Containers that simply stack their children vertically in the preview.
_VERTICAL = ("Column", "Container", "SafeArea", "Stack", "Expanded", "Flexible")


def tkinter_available() -> bool:
    """Whether Tkinter can be imported (a display is a separate question)."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


def place_flow(
    frame: Any, cells: list[Any], gap: int, run_gap: int, align: str = "start"
) -> None:
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
    """Structural fingerprint of a tree: type, id and shape, but no values.

    Two trees with the same skeleton can be patched into one another; anything
    else needs a rebuild.
    """
    props = node.get("props", {})
    # Props that change which Tk widgets exist (a refresh button, swipe
    # buttons) are structure too.
    extra = tuple(
        bool(props.get(name))
        for name in ("refreshable", "refreshing", "swipe_left", "swipe_right")
        if name in props
    )
    if node.get("type") == "PageView":
        # Another page is another subtree: it cannot be patched into this one.
        extra += (props.get("value"), props.get("item_count"), props.get("loop"))
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

    def __init__(self, app: App, *, title: str = "", scale: float = 1.0) -> None:
        import tkinter as tk

        self.app = app
        self._tk = tk
        self.root = tk.Tk()
        self.root.title(title or app.name)
        width, height = (int(value * scale) for value in _WINDOW)
        self.root.geometry(f"{width}x{height}")
        self.root.configure(bg=_PALETTE["chrome"])
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        header = tk.Frame(self.root, bg=_PALETTE["chrome"])
        header.pack(fill="x")
        self._title = tk.Label(
            header,
            text=app.name,
            bg=_PALETTE["chrome"],
            fg=_PALETTE["text"],
            font=("TkDefaultFont", 10, "bold"),
            anchor="w",
            padx=12,
            pady=6,
        )
        self._title.pack(side="left")
        self._back = tk.Button(header, text="← back", command=self.back, relief="flat")
        # Packed and forgotten dynamically, so it only shows when it works.

        self._canvas = tk.Frame(self.root, bg=_PALETTE["bg"])
        self._canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._status = tk.Label(
            self.root,
            text="",
            bg=_PALETTE["chrome"],
            fg=_PALETTE["muted"],
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
        self._build(self._canvas, tree)

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

        if kind in ("Icon", "IconButton", "AutoComplete", "RangeSlider", "PageView", "Chart"):
            patch_extra_gui(self, node)
        elif kind == "RatingBar" and widget is not None:
            self._paint_stars(widget, props.get("rating", 0))
        elif widget is not None:
            state = "normal" if node.get("enabled", True) else "disabled"
            if kind in ("Button", "TextInput", "Switch"):
                widget.configure(state=state)
            if kind in ("Label", "Button"):
                widget.configure(text=text_value(props.get("text", "")))
            elif kind == "ProgressBar":
                if not props.get("indeterminate"):
                    widget.configure(value=float(props.get("value", 0)))
            elif kind == "Switch":
                variable = self._variables.get(node["id"])
                checked = bool(props.get("checked"))
                if variable is not None and variable.get() != checked:
                    variable.set(checked)
                widget.configure(text="on" if checked else "off")
            elif kind == "TextInput":
                variable = self._variables.get(node["id"])
                value = text_value(props.get("value", ""))
                # Never write into the field being typed in: it would move the
                # caret to the end after every keystroke.
                focused = self.root.focus_get() is widget
                if variable is not None and not focused and variable.get() != value:
                    variable.set(value)

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
        background = self._background(style)
        pad = 2

        if kind == "ScrollView":
            if props.get("horizontal"):
                # No horizontal scrolling in the preview; a plain row keeps the
                # arrangement honest, which is what the picture is for.
                self._build_row(parent, node, background)
            else:
                self._build_scroll(parent, node, background)
            return

        if kind in ("Icon", "IconButton", "AutoComplete", "RangeSlider", "PageView", "Chart"):
            build_extra_gui(self, parent, node, background)
            return

        if kind in _VERTICAL:
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="both" if kind != "Column" else "x", expand=kind != "Column", pady=pad)
            self._build_children(frame, node, background)
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
            colour = _PALETTE["line"]
            tk.Frame(parent, height=thickness, bg=colour).pack(fill="x", pady=6)
            return

        if kind == "Label":
            label = tk.Label(
                parent,
                text=text_value(props.get("text", "")),
                bg=background,
                fg=self._colour(style.get("color"), _PALETTE["text"]),
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
            button.pack(fill="x", pady=pad)
            self._widgets[widget_id] = button
            return

        if kind == "TextInput":
            variable = tk.StringVar(value=text_value(props.get("value", "")))
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
            if props.get("placeholder") and not props.get("value"):
                self._show_placeholder(entry, str(props["placeholder"]))
            return

        if kind == "Switch":
            variable = tk.BooleanVar(value=bool(props.get("checked")))
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
                fg=_PALETTE["muted"],
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
            variable = tk.BooleanVar(value=bool(props.get("checked")))
            box = tk.Checkbutton(
                parent,
                variable=variable,
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
            variable = tk.DoubleVar(value=float(props.get("value", 0)))
            scale = tk.Scale(
                parent,
                from_=float(props.get("minimum", 0)),
                to=float(props.get("maximum", 100)),
                orient="horizontal",
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
            colour = self._colour(style.get("color"), _PALETTE["text"])
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
            options = [str(o) for o in (props.get("options") or [])]
            fallback = options[0] if options else ""
            variable = tk.StringVar(value=text_value(props.get("value", fallback)))
            menu = tk.OptionMenu(
                parent,
                variable,
                *(options or [""]),
                command=lambda v: self._dispatch(widget_id, "change", str(v)),
            )
            menu.pack(fill="x", pady=pad)
            self._widgets[widget_id] = menu
            self._variables[widget_id] = variable
            return

        if kind in ("Chip", "Badge"):
            tk.Label(
                parent,
                text=text_value(props.get("text", "")),
                bg=background,
                fg=self._colour(style.get("color"), _PALETTE["text"]),
                anchor="w",
            ).pack(fill="x", pady=pad)
            return

        if kind == "Stepper":
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            tk.Button(
                frame,
                text="-",
                command=lambda: self._dispatch(widget_id, "decrement", ""),
            ).pack(side="left")
            tk.Label(frame, text=text_value(props.get("value", 0)), bg=background).pack(
                side="left", padx=8
            )
            tk.Button(
                frame,
                text="+",
                command=lambda: self._dispatch(widget_id, "increment", ""),
            ).pack(side="left")
            return

        if kind == "SearchBar":
            variable = tk.StringVar(value=text_value(props.get("value", "")))
            entry = tk.Entry(parent, textvariable=variable)
            entry.pack(fill="x", pady=pad)
            variable.trace_add(
                "write", lambda *_: self._dispatch(widget_id, "change", variable.get())
            )
            self._widgets[widget_id] = entry
            return

        if kind == "RadioButton":
            tk.Radiobutton(
                parent,
                text=text_value(props.get("text", "")),
                value=text_value(props.get("text", "")),
                bg=background,
                command=lambda: self._dispatch(widget_id, "press", ""),
            ).pack(anchor="w")
            return

        if kind == "RadioGroup":
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            self._build_children(frame, node, background)
            return

        if kind == "SegmentedButtons":
            frame = tk.Frame(parent, bg=background)
            frame.pack(fill="x", pady=pad)
            selected = text_value(props.get("value", ""))
            wrap = bool(props.get("wrap"))
            buttons = []
            for option in props.get("options") or []:
                button = tk.Button(
                    frame,
                    text=str(option),
                    relief="sunken" if str(option) == selected else "raised",
                    command=lambda v=str(option): self._dispatch(widget_id, "change", v),
                )
                buttons.append(button)
                if not wrap:  # one row; Tk never squeezes a word into a column of letters
                    button.pack(side="left", expand=True, fill="x")
            if wrap:
                self._flow(frame, buttons, 0, 0, "start")
            return

        if kind == "ProgressText":
            label_text = text_value(props.get("text", ""))
            tk.Label(parent, text=label_text, bg=background, anchor="w").pack(
                fill="x", pady=pad
            )
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

        if kind == "Avatar":
            tk.Label(
                parent,
                text=text_value(props.get("text", "?")),
                bg=self._colour(props.get("background"), "#3F51B5"),
                fg=self._colour(props.get("color"), "#FFFFFF"),
                width=4,
                height=2,
            ).pack(pady=pad)
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
                text=text_value(props.get("title", "")),
                command=lambda: self._dispatch(widget_id, "press", ""),
                anchor="w",
            )
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
            for option in props.get("options", ()):
                selected = option == props.get("value")
                tk.Button(
                    bar,
                    text=str(option),
                    relief="sunken" if selected else "raised",
                    command=lambda option=str(option): self._dispatch(widget_id, "change", option),
                ).pack(side="left", expand=True, fill="x")
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

        tk.Label(parent, text=f"<{kind}>", fg=_PALETTE["muted"], bg=background).pack(anchor="w")

    def _build_children(self, frame: tk.Misc, node: dict[str, Any], background: str) -> None:
        """Stack children vertically, honouring the container's spacing."""
        spacing = int(node.get("props", {}).get("spacing", 0))
        for index, child in enumerate(node.get("children", ())):
            if index and spacing:
                self._tk.Frame(frame, height=spacing, bg=background).pack()
            self._build(frame, child)

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
        entry.configure(fg=_PALETTE["muted"])

        def on_focus_in(_event: object) -> None:
            if entry.get() == text:
                entry.delete(0, "end")
                entry.configure(fg=_PALETTE["text"])

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
        return self._colour(style.get("background"), _PALETTE["bg"])

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
