"""Desktop renderer helpers for the native advanced widgets (no CDN assets)."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from html import escape
from typing import Any

from .drawing import drawing_svg

EXTRA_SCRIPT = r"""
function pmRange(input, commit) {
  const group = input.closest('[data-range]');
  if (!group || group.dataset.disabled === '1') return;
  const lo = group.querySelector('[data-thumb="low"]');
  const hi = group.querySelector('[data-thumb="high"]');
  const min = Number(input.min), max = Number(input.max), step = Number(group.dataset.step);
  let n = Number(input.value);
  n = n >= max ? max : Math.max(min, Math.min(max, min + Math.floor((n-min)/step + .5)*step));
  input.value = input === lo ? Math.min(Number(hi.value), n) : Math.max(Number(lo.value), n);
  group.querySelector('output').textContent = Number(lo.value) + ' – ' + Number(hi.value);
  const band = group.querySelector('[data-band]');
  band.style.left = ((Number(lo.value)-min)/(max-min)*100) + '%';
  band.style.width = ((Number(hi.value)-Number(lo.value))/(max-min)*100) + '%';
  if (commit) send(group.dataset.wid, 'change', JSON.stringify([Number(lo.value), Number(hi.value)]));
}
function pmRangeKey(event, input) {
  if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const step = Number(input.closest('[data-range]').dataset.step);
  let n = Number(input.value);
  if (event.key === 'Home') n = Number(input.min);
  else if (event.key === 'End') n = Number(input.max);
  else n += ['ArrowRight','ArrowUp'].includes(event.key) ? step : -step;
  input.value = Math.max(Number(input.min), Math.min(Number(input.max), n));
  pmRange(input, true);
}
function pmPage(group, delta) {
  if (!group || group.dataset.disabled === '1') return;
  const count = Number(group.dataset.count), index = Number(group.dataset.index);
  let target = index + delta;
  if (group.dataset.loop === '1') target = (target + count) % count;
  if (target >= 0 && target < count && target !== index) send(group.dataset.wid, 'change', String(target));
}
let pmDrag = null;
document.addEventListener('pointerdown', function (e) {
  const g = e.target.closest('[data-pageview]');
  if (!g || g.dataset.disabled === '1' || e.target.closest('input,textarea,select,[data-range]')) return;
  pmDrag = {g, x:e.clientX, y:e.clientY, id:e.pointerId, horizontal:false};
});
document.addEventListener('pointermove', function (e) {
  if (!pmDrag || pmDrag.id !== e.pointerId) return;
  const dx = e.clientX-pmDrag.x, dy = e.clientY-pmDrag.y;
  if (Math.abs(dx)>12 && Math.abs(dx)>Math.abs(dy)*1.3) pmDrag.horizontal = true;
  if (pmDrag.horizontal) { e.preventDefault(); pmDrag.g.dataset.moved='1'; }
}, {passive:false});
document.addEventListener('pointerup', function (e) {
  if (!pmDrag || pmDrag.id !== e.pointerId) return;
  const d=pmDrag; pmDrag=null;
  if (d.horizontal && Math.abs(e.clientX-d.x)>Math.max(40,d.g.clientWidth*.15)) {
    e.preventDefault(); pmPage(d.g, e.clientX<d.x ? 1 : -1);
  }
});
document.addEventListener('pointercancel', function () { pmDrag=null; });
document.addEventListener('click', function (e) {
  const g=e.target.closest('[data-pageview]');
  if (g && g.dataset.moved==='1') { e.preventDefault(); e.stopPropagation(); g.dataset.moved='0'; }
}, true);
"""


def render_extra_html(
    node: dict[str, Any], render: Callable[[dict[str, Any]], str], css: str
) -> str:
    kind, props = node["type"], node.get("props", {})
    wid = escape(str(node.get("id", "")), quote=True)
    disabled = "" if node.get("enabled", True) else " disabled"
    label = escape(str(props.get("label", "")), quote=True)
    if kind in ("RangeSlider", "PageView", "Chart", "AutoComplete"):
        # A column aligns its children to the start and would shrink these to nothing.
        css = f"width:100%;{css}"
    if kind in ("Icon", "IconButton", "Chart"):
        svg = drawing_svg(props["drawing"], label=str(props.get("label", "")))
        if kind == "IconButton":
            return (
                f'<button class="w" data-wid="{wid}" title="{label}" aria-label="{label}" '
                f'{disabled} style="padding:0;display:flex;align-items:center;justify-content:center;{css}" '
                f"onclick=\"send(this.dataset.wid,'press','')\">"
                f'<span style="display:block;width:24px;height:24px">{svg}</span></button>'
            )
        return f'<div data-wid="{wid}" style="{css}">{svg}</div>'
    if kind == "AutoComplete":
        value = escape(str(props.get("value", "")), quote=True)
        placeholder = escape(str(props.get("placeholder", "")), quote=True)
        options = "".join(
            f'<option value="{escape(str(o), quote=True)}"></option>'
            for o in props.get("options", [])
        )
        limit = int(props.get("max_length", 0) or 0)
        max_attr = f' maxlength="{limit}"' if limit > 0 else ""
        # A datalist is keyboard-usable and has no dependency on a JS UI library.
        code = (
            "const list=document.getElementById(this.getAttribute('list'));"
            "if(list && Array.from(list.options).some(o=>o.value===this.value))"
            "send(this.dataset.wid,'select',this.value)"
        )
        input_type = "password" if props.get("password") else "text"
        return (
            f'<input class="w" type="{input_type}" data-wid="{wid}" list="{wid}:suggestions" '
            f'value="{value}" placeholder="{placeholder}"{disabled}{max_attr} style="{css}" '
            f'oninput="send(this.dataset.wid,\'change\',this.value)" onchange="{escape(code, quote=True)}">'
            f'<datalist id="{wid}:suggestions">{options}</datalist>'
        )
    if kind == "RangeSlider":
        minimum, maximum = float(props["minimum"]), float(props["maximum"])
        low, high, step = float(props["low"]), float(props["high"]), float(props["step"])
        left = (low - minimum) / (maximum - minimum) * 100
        band = (high - low) / (maximum - minimum) * 100
        # CSS is local and embedded so render_html() also produces a usable file.
        style = """<style>
.pm-range-track{position:relative;height:40px;margin:0 12px}
.pm-range-track input{position:absolute;left:0;top:0;margin:0;width:100%;height:40px;background:none;appearance:none;pointer-events:none}
.pm-range-track input::-webkit-slider-runnable-track{height:4px;background:transparent}
.pm-range-track input::-moz-range-track{height:4px;background:transparent}
.pm-range-track input::-webkit-slider-thumb{appearance:none;pointer-events:auto;width:18px;height:18px;border-radius:50%;margin-top:-7px;background:var(--primary,#3F51B5);border:0;cursor:pointer}
.pm-range-track input::-moz-range-thumb{pointer-events:auto;width:18px;height:18px;border-radius:50%;background:var(--primary,#3F51B5);border:0;cursor:pointer}
.pm-range-track input:focus-visible{outline:1px dashed var(--primary,#3F51B5)}
</style>"""
        inputs = "".join(
            f'<input type="range" min="{minimum:g}" max="{maximum:g}" step="any" value="{v:g}" '
            f'data-wid="{wid}:{thumb}" data-thumb="{thumb}" aria-label="{label} {thumb}"{disabled} '
            f'oninput="pmRange(this,false)" onchange="pmRange(this,true)" onkeydown="pmRangeKey(event,this)">'
            for thumb, v in (("low", low), ("high", high))
        )
        return (
            style + f'<div data-range="1" data-wid="{wid}" data-step="{step:g}" '
            f'data-disabled="{0 if not disabled else 1}" style="{css}">'
            '<div class="pm-range-track"><span style="position:absolute;top:18px;left:0;right:0;'
            'height:4px;background:var(--muted,#757575);opacity:.3"></span>'
            f'<span data-band style="position:absolute;top:18px;left:{left:g}%;width:{band:g}%;'
            f'height:4px;background:var(--primary,#3F51B5)"></span>{inputs}</div>'
            f'<output style="display:block;text-align:center">{low:g} – {high:g}</output></div>'
        )
    if kind == "PageView":
        count, index = int(props["item_count"]), int(props["value"])
        loop = bool(props.get("loop"))
        prev_off = " disabled" if disabled or (index == 0 and not loop) else ""
        next_off = " disabled" if disabled or (index == count - 1 and not loop) else ""
        page = "".join(render(child) for child in node.get("children", []))
        return (
            f'<div data-pageview="1" data-wid="{wid}" data-index="{index}" data-count="{count}" '
            f'data-loop="{int(loop)}" data-disabled="{int(bool(disabled))}" tabindex="0" '
            f'role="region" aria-label="Page {index + 1} of {count}" '
            f'style="overflow:hidden;position:relative;touch-action:pan-y;{css}" '
            "onkeydown=\"if(event.target===this && ['ArrowLeft','ArrowRight'].includes(event.key))"
            "{event.preventDefault();pmPage(this,event.key==='ArrowRight'?1:-1)}\">"
            f'{page}<div style="position:absolute;bottom:3px;right:5px;display:flex;gap:4px">'
            f'<button aria-label="Previous page"{prev_off} onclick="pmPage(this.closest(\'[data-pageview]\'),-1)">←</button>'
            f'<button aria-label="Next page"{next_off} onclick="pmPage(this.closest(\'[data-pageview]\'),1)">→</button>'
            "</div></div>"
        )
    raise ValueError(f"not an advanced HTML widget: {kind}")


def extra_ascii(node: dict[str, Any]) -> list[str]:
    kind, props = node["type"], node.get("props", {})
    if kind == "Icon":
        return [f"[icon:{props['name']}]"]
    if kind == "IconButton":
        suffix = " ✗" if not node.get("enabled", True) else ""
        return [f"({props['label']} [{props['name']}]){suffix}"]
    if kind == "AutoComplete":
        value = props.get("value") or props.get("placeholder") or "…"
        if props.get("password") and props.get("value"):
            value = "•" * len(str(props["value"]))
        return [f"⎡{value}⎦ ↧ {len(props.get('options', []))} suggestions"]
    if kind == "RangeSlider":
        return [
            f"[● {props['low']:g} ━━ {props['high']:g} ●] ({props['minimum']:g}..{props['maximum']:g})"
        ]
    if kind == "Chart":
        title = props.get("title") or (str(props["kind"]).capitalize() + "Chart")
        rows = [str(title)]
        values = props.get("data", [])
        if not values:
            return [*rows, "(no data)"]
        scale = max(abs(float(v)) for v in values) or 1
        total = sum(values) or 1
        for label, value in zip(props["labels"], values, strict=True):
            if props["kind"] == "pie":
                rows.append(f"{label}: {value:g} ({value / total:.0%})")
            elif props["kind"] == "bar":
                rows.append(
                    f"{label}: {'−' if value < 0 else ''}{'█' * round(abs(value) / scale * 16)} {value:g}"
                )
            else:
                rows.append(f"{label}: {value:g}")
        return rows
    raise ValueError(f"not an advanced ASCII widget: {kind}")


def _tk_color(color: str, fallback: str) -> str:
    if color.startswith("#") and len(color) == 9:
        return "#" + color[3:]
    return color or fallback


def paint_tk(
    canvas: Any, drawing: dict[str, Any], width: float, height: float, *, button: bool = False
) -> None:
    canvas.delete("drawing")
    logical_w, logical_h = float(drawing["width"]), float(drawing["height"])
    scale = min(width / logical_w, height / logical_h)
    if button:
        scale = min(scale, 1)
    ox, oy = (width - logical_w * scale) / 2, (height - logical_h * scale) / 2

    def xy(x: float, y: float) -> tuple[float, float]:
        return ox + x * scale, oy + y * scale

    for s in drawing.get("shapes", []):
        color = _tk_color(str(s.get("color", "")), "#212121")
        kind = s["type"]
        if kind == "line":
            points = [coordinate for x, y in s["points"] for coordinate in xy(x, y)]
            if len(points) >= 4:
                canvas.create_line(
                    *points,
                    fill=color,
                    width=max(1, s.get("width", 2) * scale),
                    capstyle="round",
                    joinstyle="round",
                    tags="drawing",
                )
        elif kind == "rect":
            canvas.create_rectangle(
                *xy(s["x"], s["y"]),
                *xy(s["x"] + s["w"], s["y"] + s["h"]),
                fill=color,
                outline="",
                tags="drawing",
            )
        elif kind in ("circle", "sector"):
            box = (*xy(s["x"] - s["r"], s["y"] - s["r"]), *xy(s["x"] + s["r"], s["y"] + s["r"]))
            if kind == "sector" and s["sweep"] < 359.999:
                canvas.create_arc(
                    *box,
                    start=-s["start"],
                    extent=-s["sweep"],
                    fill=color,
                    outline="",
                    style="pieslice",
                    tags="drawing",
                )
            else:
                canvas.create_oval(
                    *box,
                    fill=color if s.get("fill", True) else "",
                    outline=color,
                    width=max(1, s.get("width", 1) * scale),
                    tags="drawing",
                )
        elif kind == "text":
            anchor = {"center": "n", "end": "ne"}.get(s.get("align"), "nw")
            canvas.create_text(
                *xy(s["x"], s["y"]),
                text=s["text"],
                fill=color,
                anchor=anchor,
                font=("TkDefaultFont", max(1, round(s.get("size", 12) * scale))),
                tags="drawing",
            )


def build_extra_gui(preview: Any, parent: Any, node: dict[str, Any], background: str) -> None:
    tk, props, kind = preview._tk, node.get("props", {}), node["type"]
    wid = str(node["id"])
    enabled = bool(node.get("enabled", True))
    state = "normal" if enabled else "disabled"
    if kind in ("Icon", "IconButton", "Chart"):
        size = int(props.get("size", 24))
        width = 320 if kind == "Chart" else size
        height = int(props.get("height", 220)) if kind == "Chart" else size
        bg = _tk_color(str(props.get("background", "")), background)
        canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            takefocus=1 if kind == "IconButton" else 0,
        )
        canvas.pack(fill="x" if kind == "Chart" else "none", pady=2)
        preview._widgets[wid] = canvas
        preview._extra_states[wid] = {"node": node}

        def redraw(_event: Any = None) -> None:
            data = preview._extra_states[wid]["node"]
            paint_tk(
                canvas,
                data["props"]["drawing"],
                max(1, canvas.winfo_width()),
                max(1, canvas.winfo_height()),
                button=kind == "IconButton",
            )

        canvas.bind("<Configure>", redraw)
        redraw()
        if kind == "IconButton":

            def press(_event: Any = None) -> None:
                if preview._extra_states[wid]["node"].get("enabled", True):
                    preview._dispatch(wid, "press", "")

            canvas.bind("<Button-1>", press)
            canvas.bind("<space>", press)
            canvas.bind("<Return>", press)
        return
    if kind == "AutoComplete":
        frame = tk.Frame(parent, bg=background)
        frame.pack(fill="x", pady=2)
        variable = tk.StringVar(value=str(props.get("value", "")))
        entry = tk.Entry(
            frame, textvariable=variable, state=state, show="•" if props.get("password") else ""
        )
        entry.pack(fill="x")
        choices = tk.Listbox(frame, height=3, exportselection=False)
        preview._widgets[wid], preview._variables[wid] = entry, variable
        preview._extra_states[wid] = {"node": node, "choices": choices}

        def suggestions() -> None:
            data = preview._extra_states[wid]["node"]
            text = variable.get()
            choices.delete(0, "end")
            if len(text) >= int(data["props"].get("threshold", 1)):
                for option in data["props"].get("options", []):
                    if text.casefold() in option.casefold():
                        choices.insert("end", option)
            if choices.size() and entry.focus_get() in (entry, choices):
                choices.pack(fill="x")
            else:
                choices.pack_forget()

        def changed(*_args: Any) -> None:
            if not preview._patching:
                suggestions()
                preview._dispatch(wid, "change", variable.get())

        variable.trace_add("write", changed)
        entry.bind("<FocusIn>", lambda _e: suggestions())

        def choose(_event: Any = None) -> None:
            selection = choices.curselection()
            if selection and preview._extra_states[wid]["node"].get("enabled", True):
                value = str(choices.get(selection[0]))
                preview._dispatch(wid, "select", value)
                choices.pack_forget()
                entry.focus_set()

        choices.bind("<<ListboxSelect>>", choose)
        entry.bind("<Down>", lambda _e: choices.focus_set() if choices.size() else None)
        choices.bind("<Return>", choose)
        return
    if kind == "RangeSlider":
        canvas = tk.Canvas(
            parent, width=320, height=64, bg=background, highlightthickness=0, takefocus=1
        )
        canvas.pack(fill="x", pady=2)
        data: dict[str, Any] = {
            "node": node,
            "low": float(props["low"]),
            "high": float(props["high"]),
            "active": "low",
        }
        preview._widgets[wid], preview._extra_states[wid] = canvas, data

        def redraw(_event: Any = None) -> None:
            canvas.delete("all")
            p = data["node"]["props"]
            width = max(80, canvas.winfo_width())
            span = p["maximum"] - p["minimum"]
            x1 = 20 + (data["low"] - p["minimum"]) / span * (width - 40)
            x2 = 20 + (data["high"] - p["minimum"]) / span * (width - 40)
            canvas.create_line(20, 25, width - 20, 25, fill="#BDBDBD", width=4)
            canvas.create_line(x1, 25, x2, 25, fill="#3F51B5", width=4)
            for x in (x1, x2):
                canvas.create_oval(x - 9, 16, x + 9, 34, fill="#3F51B5", outline="")
            canvas.create_text(width / 2, 52, text=f"{data['low']:g} – {data['high']:g}")

        def move(event: Any) -> None:
            if not data["node"].get("enabled", True):
                return
            p = data["node"]["props"]
            width = max(80, canvas.winfo_width())
            value = p["minimum"] + max(0, min(1, (event.x - 20) / (width - 40))) * (
                p["maximum"] - p["minimum"]
            )
            value = (
                value
                if value == p["maximum"]
                else p["minimum"] + math.floor((value - p["minimum"]) / p["step"] + 0.5) * p["step"]
            )
            value = max(p["minimum"], min(p["maximum"], value))
            data[data["active"]] = (
                min(data["high"], value) if data["active"] == "low" else max(data["low"], value)
            )
            redraw()

        def begin(event: Any) -> None:
            p = data["node"]["props"]
            width = max(80, canvas.winfo_width())
            value = p["minimum"] + (event.x - 20) / (width - 40) * (p["maximum"] - p["minimum"])
            data["active"] = (
                "low" if abs(value - data["low"]) <= abs(value - data["high"]) else "high"
            )
            canvas.focus_set()
            move(event)

        def commit(_event: Any = None) -> None:
            if data["node"].get("enabled", True):
                preview._dispatch(wid, "change", json.dumps([data["low"], data["high"]]))

        def key(event: Any) -> None:
            if not data["node"].get("enabled", True):
                return
            p = data["node"]["props"]
            if event.keysym in ("Up", "Down"):
                data["active"] = "high" if event.keysym == "Up" else "low"
            elif event.keysym in ("Left", "Right"):
                field = data["active"]
                value = data[field] + p["step"] * (1 if event.keysym == "Right" else -1)
                value = max(p["minimum"], min(p["maximum"], value))
                data[field] = (
                    min(data["high"], value) if field == "low" else max(data["low"], value)
                )
                commit()
            redraw()

        canvas.bind("<Configure>", redraw)
        canvas.bind("<Button-1>", begin)
        canvas.bind("<B1-Motion>", move)
        canvas.bind("<ButtonRelease-1>", commit)
        canvas.bind("<Key>", key)
        data["redraw"] = redraw
        redraw()
        return
    if kind == "PageView":
        frame = tk.Frame(parent, bg=background)
        frame.pack(fill="x", pady=2)
        preview._widgets[wid] = frame
        for child in node.get("children", []):
            preview._build(frame, child)
        row = tk.Frame(frame, bg=background)
        row.pack(fill="x")
        index, count, loop = int(props["value"]), int(props["item_count"]), bool(props.get("loop"))
        for delta, text in ((-1, "←"), (1, "→")):
            target = (index + delta) % count if loop else index + delta
            tk.Button(
                row,
                text=text,
                state="normal" if enabled and 0 <= target < count else "disabled",
                command=lambda target=target: preview._dispatch(wid, "change", str(target)),
            ).pack(side="left")
        tk.Label(row, text=f"{index + 1} / {count}", bg=background).pack(side="left", padx=8)
        return
    raise ValueError(f"not an advanced GUI widget: {kind}")


def patch_extra_gui(preview: Any, node: dict[str, Any]) -> None:
    wid, props = str(node["id"]), node.get("props", {})
    data = preview._extra_states.get(wid)
    if data is None:
        return
    data["node"] = node
    widget = preview._widgets.get(wid)
    if widget is None:
        return
    kind = node["type"]
    if kind in ("Icon", "IconButton", "Chart"):
        paint_tk(
            widget,
            props["drawing"],
            max(1, widget.winfo_width()),
            max(1, widget.winfo_height()),
            button=kind == "IconButton",
        )
        if props.get("background"):
            widget.configure(bg=_tk_color(str(props["background"]), "#FFFFFF"))
    elif kind == "RangeSlider":
        data["low"], data["high"] = float(props["low"]), float(props["high"])
        data["redraw"]()
    elif kind == "AutoComplete":
        widget.configure(state="normal" if node.get("enabled", True) else "disabled")
        variable = preview._variables[wid]
        value = str(props.get("value", ""))
        if variable.get() != value:
            variable.set(value)


def extra_mockup(layout: Any, kind: str, node: dict[str, Any], w: float, fill: bool) -> Any:
    from .mockup import _Box, _rgba

    props, style = node.get("props", {}), node.get("style", {})
    if kind == "AutoComplete":
        return layout._text_input("TextInput", props, style, w, fill)
    if kind == "RangeSlider":
        width = w if fill else min(w, 300)
        minimum, maximum = props["minimum"], props["maximum"]

        def paint(p: Any, x: float, y: float, b: Any) -> None:
            start, end = x + 20, x + b.w - 20
            lo = start + (props["low"] - minimum) / (maximum - minimum) * (end - start)
            hi = start + (props["high"] - minimum) / (maximum - minimum) * (end - start)
            p.line([(start, y + 25), (end, y + 25)], layout.p["DIVIDER"], 4)
            p.line([(lo, y + 25), (hi, y + 25)], layout.p["PRIMARY"], 4)
            p.circle(lo, y + 25, 9, layout.p["PRIMARY"])
            p.circle(hi, y + 25, 9, layout.p["PRIMARY"])
            p.lines(
                x,
                y + 44,
                b.w,
                [f"{props['low']:g} – {props['high']:g}"],
                12,
                layout.p["TEXT"],
                align="center",
            )

        return _Box(width, 64, paint)
    if kind in ("Icon", "IconButton", "Chart"):
        drawing = props["drawing"]
        height = (
            float(props.get("height", 220)) if kind == "Chart" else float(props.get("size", 24))
        )
        width = (w if fill else min(w, 320)) if kind == "Chart" else float(props.get("size", 24))

        def paint(p: Any, x: float, y: float, b: Any) -> None:
            scale = min(b.w / drawing["width"], b.h / drawing["height"])
            if kind == "IconButton":
                scale = min(scale, 1)
            ox, oy = (
                x + (b.w - drawing["width"] * scale) / 2,
                y + (b.h - drawing["height"] * scale) / 2,
            )

            def xy(px: float, py: float) -> tuple[float, float]:
                return ox + px * scale, oy + py * scale

            for shape in drawing.get("shapes", []):
                color = _rgba(shape.get("color"), layout.p["TEXT"])
                t = shape["type"]
                if t == "line":
                    p.line(
                        [xy(px, py) for px, py in shape["points"]],
                        color,
                        shape.get("width", 2) * scale,
                    )
                elif t == "rect":
                    px, py = xy(shape["x"], shape["y"])
                    p.rect(px, py, shape["w"] * scale, shape["h"] * scale, color)
                elif t == "circle":
                    px, py = xy(shape["x"], shape["y"])
                    p.circle(
                        px,
                        py,
                        shape["r"] * scale,
                        color if shape.get("fill", True) else None,
                        color,
                        shape.get("width", 1) * scale,
                    )
                elif t == "sector" and shape["sweep"] > 0:
                    points = [xy(shape["x"], shape["y"])]
                    steps = max(2, math.ceil(shape["sweep"] / 3))
                    for i in range(steps + 1):
                        angle = math.radians(shape["start"] + shape["sweep"] * i / steps)
                        points.append(
                            xy(
                                shape["x"] + shape["r"] * math.cos(angle),
                                shape["y"] + shape["r"] * math.sin(angle),
                            )
                        )
                    p.polygon(points, color)
                elif t == "text":
                    size = shape.get("size", 12) * scale
                    text_width = p.fonts.width(str(shape["text"]), size)
                    px, py = xy(shape["x"], shape["y"])
                    if shape.get("align") == "center":
                        px -= text_width / 2
                    elif shape.get("align") == "end":
                        px -= text_width
                    p.text(px, py, str(shape["text"]), size, color)

        return _Box(width, height, paint)
    return None
