"""A browser-based interactive preview.

The Tk preview needs a display; this one needs a browser, which is what a
remote machine, a container or a Codespace actually has. It serves the running
application over HTTP: the widget tree is rendered as HTML, interactions are
posted back, and the page polls for a new tree so a timer tick or a background
update appears on its own.

Nothing is compiled or bundled — the page is a few hundred bytes of hand-written
HTML and JavaScript built from the same serialised tree the phone receives, so
the preview cannot drift from the real renderer.
"""

from __future__ import annotations

import json
import secrets
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, Protocol

from ...log import get_logger
from .contract import text_value
from .extras_preview import EXTRA_SCRIPT, render_extra_html

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..app import App

__all__ = ["WebPreview", "render_html", "serve"]

_log = get_logger("ui.web")

#: Interfaces that are only reachable from this machine. A preview bound to
#: one of them is a private developer window; anything else is a service on the
#: network and gets the full policy below.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]", ""})

#: Largest accepted /event body. The widget protocol is tiny; a multi-megabyte
#: body is either a mistake or an attempt to make the server allocate it.
MAX_EVENT_BYTES = 64 * 1024

#: A rejected request is answered without using its body, but the bytes are
#: already in the socket. A client that is still sending then sees the
#: connection reset instead of the status line (Windows: WinError 10053), so
#: the body is consumed first — bounded, so a lying ``Content-Length`` cannot
#: make the preview read forever.
DRAIN_LIMIT = 8 * 1024 * 1024
DRAIN_CHUNK = 64 * 1024


class _Readable(Protocol):
    """What draining needs: ``BinaryIO`` refuses ``BufferedIOBase.read``."""

    def read(self, size: int = ..., /) -> bytes | None: ...  # pragma: no cover


def _drain_body(rfile: _Readable, length: int, limit: int = DRAIN_LIMIT) -> int:
    """Read and discard an unused request body; return how much was consumed.

    Stops at ``limit`` bytes or at end of file, whichever comes first.
    """
    remaining = min(max(length, 0), limit)
    consumed = 0
    while remaining > 0:
        chunk = rfile.read(min(DRAIN_CHUNK, remaining))
        if not chunk:
            break
        consumed += len(chunk)
        remaining -= len(chunk)
    return consumed


def _is_loopback(host: str) -> bool:
    """Whether ``host`` names this machine only (wildcards do not)."""
    text = (host or "").strip()
    return text in LOOPBACK_HOSTS or text.startswith("127.")


def browser_url(host: str, port: int, token: str = "") -> str:
    """A URL a human can actually open in a browser.

    ``0.0.0.0`` and ``::`` are *bind* addresses ("listen on every interface");
    they are not destinations — Windows browsers refuse them outright with
    ``ERR_ADDRESS_INVALID``. Point humans at loopback instead, and keep the
    wildcard bind for containers, SSH tunnels and LAN devices.

    The session token travels in the query string, so the address printed on
    the terminal is the one that works; opening the bare host without it is
    refused when the preview is exposed.
    """
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    suffix = f"/?t={token}" if token else ""
    return f"http://{host}:{port}{suffix}"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  /* The chrome colours are custom properties, not literals baked into the
     rules: the polling loop rewrites them when App.set_theme() switches the
     palette, so the phone shell follows dark mode without a page reload. */
  :root {{ color-scheme: {color_scheme};
    --page-bg: {page_bg}; --phone-bg: {phone_bg}; --text: {text}; --bar-bg: {bar_bg};
    --line: {line}; --primary: {primary}; --muted: {muted};
    --snack-bg: {snack_bg}; --snack-fg: {snack_fg}; --snack-action: {snack_action}; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--page-bg); color: var(--text);
          font: 15px/1.45 system-ui, sans-serif; }}
  .phone {{ max-width: 420px; margin: 24px auto; background: var(--phone-bg); min-height: 80vh;
           border-radius: 22px; box-shadow: 0 6px 32px rgba(0,0,0,.18); overflow: hidden;
           display: flex; flex-direction: column; color: var(--text); }}
  .bar {{ background: var(--bar-bg); border-bottom: 1px solid var(--line); padding: 10px 16px;
          display: flex; align-items: center; gap: 12px; font-weight: 600; }}
  .bar button {{ font: inherit; font-weight: 500; border: 0; background: none;
                 color: var(--primary); cursor: pointer; padding: 0; }}
  .screen {{ padding: 16px; flex: 1; }}
  .status {{ background: var(--bar-bg); border-top: 1px solid var(--line); padding: 6px 16px;
             min-height: 28px; color: var(--muted); font-size: 13px; }}
  .row {{ display: flex; }}
  .col {{ display: flex; flex-direction: column; }}
  .grid {{ display: grid; }}
  button.w {{ font: inherit; padding: 9px 14px; border-radius: 8px; cursor: pointer;
              border: 1px solid var(--line); background: var(--bar-bg); color: var(--text);
              width: 100%; }}
  button.w:disabled {{ opacity: .45; cursor: not-allowed; }}
  input.w, textarea.w, select.w {{ font: inherit; padding: 8px 10px; border: 1px solid var(--line);
             border-radius: 8px; width: 100%; background: var(--phone-bg); color: var(--text); }}
  progress.w {{ width: 100%; height: 10px; }}
  hr.w {{ border: 0; border-top: 1px solid var(--line); margin: 8px 0; width: 100%; }}
  .muted {{ color: var(--muted); }}
  table.w {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  table.w th, table.w td {{ border: 1px solid var(--line); padding: 6px 8px; text-align: left; }}
  a.w {{ color: var(--primary); }}
  .avatar {{ display: inline-flex; align-items: center; justify-content: center;
             border-radius: 50%; font-weight: 600; }}
  .seg {{ display: flex; gap: 0; overflow-x: auto; }}
  .seg button {{ flex: 1 0 auto; border-radius: 0; width: auto; white-space: nowrap; }}
  .seg.wrap {{ flex-wrap: wrap; overflow-x: visible; }}
  .seg.wrap button {{ white-space: normal; }}
  .phone {{ position: relative; }}
  .swipe {{ width: auto; flex: 0 0 auto; padding: 9px 10px; color: #fff; border: 0; }}
  .refresh {{ text-align: center; }}
  #snack {{ position: absolute; left: 12px; right: 12px; bottom: 40px; display: none;
            align-items: center; gap: 8px; padding: 6px 8px 6px 16px; min-height: 48px;
            border-radius: 4px; background: var(--snack-bg); color: var(--snack-fg);
            box-shadow: 0 3px 10px rgba(0,0,0,.3); }}
  #snack span {{ flex: 1; }}
  #snack button {{ font: inherit; font-weight: 700; border: 0; background: none;
                   color: var(--snack-action); cursor: pointer; padding: 8px; }}
</style>
</head>
<body>
<div class="phone">
  <div class="bar"><span id="title">{title}</span>
    <button id="back" style="display:none" onclick="send('system','back','')">← back</button>
  </div>
  <div class="screen" id="screen">{body}</div>
  <div class="status" id="status"></div>
  <div id="snack"><span id="snack-text"></span>
    <button id="snack-action" onclick="send('__snackbar__','press',this.dataset.token)"></button>
    <button title="dismiss" onclick="send('__snackbar__','dismiss',this.dataset.token)"
            id="snack-close">✕</button>
  </div>
</div>
<script>
{extra_script}
let version = {version};
const token = {token_literal};
const scrolled = {{}};
function url(path, params) {{
  const query = new URLSearchParams(params || {{}});
  if (token) query.set('t', token);
  const text = query.toString();
  return text ? path + '?' + text : path;
}}
async function send(id, kind, value) {{
  const r = await fetch(url('/event'), {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json', 'X-PMB-Token': token}},
    body: JSON.stringify({{id, kind, value}})
  }});
  if (r.status !== 200) {{ document.getElementById('status').textContent =
      'refused (' + r.status + ')'; return; }}
  apply(await r.json());
}}
function applyChrome(chrome) {{
  if (!chrome) return;
  const root = document.documentElement;
  for (const name in chrome) {{
    root.style.setProperty('--' + name.replace(/_/g, '-'), chrome[name]);
  }}
  if (chrome.color_scheme) root.style.colorScheme = chrome.color_scheme;
}}
function apply(state) {{
  applyChrome(state.chrome);  // the shell follows App.set_theme() every poll
  if (state.version === version) return;
  version = state.version;
  const active = document.activeElement;
  const keep = active && active.dataset ? active.dataset.wid : null;
  const start = active && active.selectionStart;
  document.getElementById('screen').innerHTML = state.body;
  document.getElementById('title').textContent = state.title;
  document.getElementById('back').style.display = state.depth > 1 ? '' : 'none';
  document.getElementById('status').textContent = state.status || '';
  const snack = document.getElementById('snack');
  if (state.snackbar) {{
    snack.style.display = 'flex';
    document.getElementById('snack-text').textContent = state.snackbar.message;
    const action = document.getElementById('snack-action');
    action.textContent = state.snackbar.action || '';
    action.style.display = state.snackbar.action ? '' : 'none';
    action.dataset.token = state.snackbar.token;
    document.getElementById('snack-close').dataset.token = state.snackbar.token;
  }} else {{ snack.style.display = 'none'; }}
  document.querySelectorAll('[data-scroll-serial]').forEach(function (list) {{
    const serial = list.dataset.scrollSerial;
    if (serial === '0' || scrolled[list.dataset.wid] === serial) return;
    scrolled[list.dataset.wid] = serial;
    const row = list.querySelector('[data-row="' + list.dataset.scrollTo + '"]');
    if (row) row.scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
  }});
  if (keep) {{  // typing must survive a redraw
    let again = null;
    try {{
      again = document.querySelector('[data-wid="' + keep + '"]');
      if (again === null && typeof CSS !== 'undefined' && CSS.escape) {{
        again = document.querySelector('[data-wid="' + CSS.escape(keep) + '"]');
      }}
    }} catch (e) {{ again = null; }}
    if (again) {{ again.focus(); if (start != null && again.setSelectionRange)
      again.setSelectionRange(start, start); }}
  }}
}}
async function poll() {{
  try {{ apply(await (await fetch(url('/state', {{v: version}}))).json()); }}
  catch (e) {{ document.getElementById('status').textContent = 'disconnected'; }}
  setTimeout(poll, 400);
}}
poll();
</script>
</body>
</html>
"""


def _style_css(style: dict[str, Any]) -> str:
    """Translate a serialised Style into inline CSS."""
    parts: list[str] = []
    colour = style.get("color")
    if isinstance(colour, str):
        parts.append(f"color:{_css_colour(colour)}")
    background = style.get("background")
    if isinstance(background, str):
        parts.append(f"background:{_css_colour(background)}")
    if style.get("font_size"):
        parts.append(f"font-size:{style['font_size']}px")
    if style.get("bold"):
        parts.append("font-weight:700")
    if style.get("italic"):
        parts.append("font-style:italic")
    for name, prop in (("padding", "padding"), ("margin", "margin")):
        box = style.get(name)
        if isinstance(box, list) and len(box) == 4:
            left, top, right, bottom = box
            parts.append(f"{prop}:{top}px {right}px {bottom}px {left}px")
    if style.get("corner_radius"):
        parts.append(f"border-radius:{style['corner_radius']}px")
    for key, prop in (
        ("min_width", "min-width"),
        ("max_width", "max-width"),
        ("min_height", "min-height"),
        ("max_height", "max-height"),
    ):
        if style.get(key) is not None:
            parts.append(f"{prop}:{style[key]}px")
    if style.get("aspect_ratio"):
        parts.append(f"aspect-ratio:{style['aspect_ratio']:.4f}")
    for key, prop in (("width", "width"), ("height", "height")):
        value = style.get(key)
        if isinstance(value, int):
            parts.append(f"{prop}:{value}px")
        elif isinstance(value, str) and value in ("match", "fill", "match_parent"):
            parts.append(f"{prop}:100%")
    if style.get("elevation"):
        depth = int(style["elevation"])
        parts.append(f"box-shadow:0 {depth}px {depth * 2}px rgba(0,0,0,.2)")
    return ";".join(parts)


def _css_colour(value: str) -> str:
    """#AARRGGBB is an Android convention; CSS wants #RRGGBBAA."""
    if len(value) == 9 and value.startswith("#"):
        return f"#{value[3:]}{value[1:3]}"
    return value


def _alignment(value: str | None) -> str:
    return {
        "center": "center",
        "end": "flex-end",
        "space_between": "space-between",
        "stretch": "stretch",
        "start": "flex-start",
    }.get(value or "", "flex-start")





def _js_value(value: str) -> str:
    """Escape ``value`` for embedding inside a single-quoted JS string.

    Inline handlers are plain attributes: the browser decodes HTML entities
    before evaluating the JavaScript, so HTML-escaping alone was never enough
    and an option label like ``O'Reilly`` could close the JS string. This
    escapes the characters that matter inside a ''-quoted JS string first,
    then HTML-escapes the whole thing so the attribute stays well-formed.
    """
    return escape(
        str(value)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n"),
        quote=True,
    )


def render_html(node: dict[str, Any]) -> str:
    """Render one serialised widget node (and its children) as HTML."""
    if not node.get("visible", True):
        return ""

    kind = node.get("type", "Label")
    props = node.get("props", {})
    style = node.get("style", {})
    css = _style_css(style)
    widget_id = escape(str(node.get("id", "")), quote=True)
    disabled = "" if node.get("enabled", True) else " disabled"
    children = node.get("children", ())
    inner = "".join(render_html(child) for child in children)

    if kind == "List":
        # Rows are numbered so List.scroll_to() can find its target, and the
        # pull gesture becomes a button (a mouse cannot pull).
        inner = "".join(
            f'<div class="col" data-row="{index}">{render_html(child)}</div>'
            for index, child in enumerate(children)
        )
        if props.get("refreshable"):
            if props.get("refreshing"):
                head = '<div class="muted refresh">⟳ Refreshing…</div>'
            else:
                head = (
                    f'<button class="w refresh" data-wid="{widget_id}"{disabled} '
                    f"onclick=\"send(this.dataset.wid,'refresh','')\">"
                    "↻ Pull to refresh</button>"
                )
            inner = head + inner

    if kind == "List" and props.get("has_more"):
        # The browser has no "last row became visible" hook wired up: a button
        # stands in for scrolling to the end.
        loaded = int(props.get("loaded", len(children)))
        total = int(props.get("item_count", loaded))
        inner += (
            f'<button class="w" data-wid="{widget_id}" data-seen="{loaded}" '
            f"onclick=\"send(this.dataset.wid,'load_more',this.dataset.seen)\">"
            f"Load more ({loaded} of {total})</button>"
        )

    if kind == "List":
        gap = props.get("spacing", 0)
        align = _alignment(props.get("cross_align"))
        serial = int(props.get("scroll_serial", 0) or 0)
        target = int(props.get("scroll_to", -1) if props.get("scroll_to") is not None else -1)
        extra = f"gap:{gap}px;align-items:{align};{css}"
        return (
            f'<div class="col" data-wid="{widget_id}" data-scroll-to="{target}" '
            f'data-scroll-serial="{serial}" style="{extra}">{inner}</div>'
        )

    if kind in ("Column", "Container", "SafeArea", "Stack", "RadioGroup"):
        gap = props.get("spacing", 0)
        align = _alignment(props.get("cross_align"))
        extra = f"gap:{gap}px;align-items:{align};{css}"
        return f'<div class="col" style="{extra}">{inner}</div>'

    if kind == "ScrollView":
        gap = props.get("spacing", 0)
        horizontal = props.get("horizontal")
        flow = "row" if horizontal else "column"
        overflow = "overflow-x:auto" if horizontal else "overflow-y:auto"
        return (
            f'<div class="col" style="flex-direction:{flow};gap:{gap}px;{overflow};{css}">'
            f"{inner}</div>"
        )

    if kind == "Row":
        gap = props.get("spacing", 0)
        justify = _alignment(props.get("align"))
        align = _alignment(props.get("cross_align")) if props.get("cross_align") else "center"
        return (
            f'<div class="row" style="gap:{gap}px;justify-content:{justify};'
            f'align-items:{align};{css}">{inner}</div>'
        )

    if kind == "Grid":
        columns = max(1, int(props.get("columns", 2)))
        row_gap = props.get("row_spacing", 0)
        column_gap = props.get("column_spacing", 0)
        return (
            f'<div class="grid" style="grid-template-columns:repeat({columns},1fr);'
            f'row-gap:{row_gap}px;column-gap:{column_gap}px;{css}">{inner}</div>'
        )

    if kind == "Wrap":
        gap = props.get("spacing", 0)
        run_gap = props.get("run_spacing", gap)
        justify = _alignment(props.get("align"))
        return (
            f'<div class="wrap" style="display:flex;flex-wrap:wrap;gap:{run_gap}px {gap}px;'
            f'justify-content:{justify};align-items:flex-start;{css}">{inner}</div>'
        )

    if kind in ("Expanded", "Flexible"):
        flex = int(props.get("flex", 1))
        basis = "0" if props.get("fit", "tight") == "tight" else "auto"
        return f'<div style="flex:{flex} {flex} {basis};min-width:0;{css}">{inner}</div>'

    if kind == "Divider":
        if props.get("vertical"):
            width = props.get("thickness", 1)
            return (
                f'<div style="width:{width}px;align-self:stretch;background:rgba(0,0,0,.12)"></div>'
            )
        return f'<hr class="w" style="{css}">'

    if kind == "Label":
        # The id is carried through so the browser inspector shows which
        # widget a node is — the same readable ids find() uses.
        text = escape(text_value(props.get("text", "")))
        return f'<div data-wid="{widget_id}" style="{css}">{text}</div>'

    if kind == "Button":
        label = escape(text_value(props.get("text", "")))
        return (
            f'<button class="w" data-wid="{widget_id}"{disabled} style="{css}" '
            f"onclick=\"send(this.dataset.wid,'press','')\">{label}</button>"
        )

    if kind == "TextInput":
        value = escape(text_value(props.get("value", "")), quote=True)
        placeholder = escape(text_value(props.get("placeholder", "")), quote=True)
        kind_attr = "password" if props.get("password") else "text"
        if props.get("multiline"):
            body = escape(text_value(props.get("value", "")))
            return (
                f'<textarea class="w" data-wid="{widget_id}" placeholder="{placeholder}"'
                f'{disabled} style="{css}" '
                f"oninput=\"send(this.dataset.wid,'change',this.value)\">{body}</textarea>"
            )
        return (
            f'<input class="w" type="{kind_attr}" data-wid="{widget_id}" value="{value}" '
            f'placeholder="{placeholder}"{disabled} style="{css}" '
            f"oninput=\"send(this.dataset.wid,'change',this.value)\">"
        )

    if kind == "Switch":
        checked = " checked" if props.get("checked") else ""
        return (
            f'<label style="display:flex;gap:8px;align-items:center;{css}">'
            f'<input type="checkbox" data-wid="{widget_id}"{checked}{disabled} '
            f"onchange=\"send(this.dataset.wid,'toggle',this.checked?'true':'false')\">"
            f'<span class="muted">{"on" if props.get("checked") else "off"}</span></label>'
        )

    if kind == "ProgressBar":
        if props.get("indeterminate"):
            return f'<progress class="w" style="{css}"></progress>'
        maximum = props.get("maximum", 100) or 100
        value = props.get("value", 0)
        return f'<progress class="w" max="{maximum}" value="{value}" style="{css}"></progress>'

    if kind == "Image":
        source = escape(text_value(props.get("source", "")), quote=True)
        # http(s)/data sources render directly; APK-local asset paths degrade
        # to the message on the right via the onerror fallback.
        onerror = "this.replaceWith(document.createTextNode('[image unavailable]'))"
        return (
            f'<div style="{css}">'
            f'<img data-wid="{widget_id}" src="{source}" alt="" '
            f'style="max-width:100%;display:block" onerror="{onerror}">'
            f"</div>"
        )

    if kind == "Spacer":
        return f'<div style="height:{props.get("size", 8)}px;flex:0 0 auto"></div>'

    if kind == "Slider":
        minimum = props.get("minimum", 0)
        maximum = props.get("maximum", 100)
        value = props.get("value", 0)
        return (
            f'<input class="w" type="range" data-wid="{widget_id}" min="{minimum}" '
            f'max="{maximum}" value="{value}"{disabled} style="{css}" '
            f"oninput=\"send(this.dataset.wid,'change',this.value)\">"
        )

    if kind == "Checkbox":
        checked = " checked" if props.get("checked") else ""
        return (
            f'<label style="display:flex;gap:8px;align-items:center;{css}">'
            f'<input type="checkbox" data-wid="{widget_id}"{checked}{disabled} '
            f"onchange=\"send(this.dataset.wid,'toggle',this.checked?'true':'false')\">"
            f"</label>"
        )

    if kind == "RatingBar":
        rating = props.get("rating", 0)
        maximum = int(props.get("maximum", 5) or 5)
        stars = "".join(
            f'<button type="button" data-wid="{widget_id}"{disabled} '
            f"onclick=\"send(this.dataset.wid,'change','{i}')\">"
            f"{'★' if i <= float(rating or 0) else '☆'}</button>"
            for i in range(1, maximum + 1)
        )
        return f'<div style="display:flex;gap:2px;{css}">{stars}</div>'

    if kind == "Dropdown":
        options = [str(o) for o in (props.get("options") or [])]
        labels = [str(lbl) for lbl in (props.get("labels") or options)]
        # Pad labels in case someone crafted the props by hand.
        while len(labels) < len(options):
            labels.append(options[len(labels)])
        selected = text_value(props.get("value", ""))
        items = "".join(
            f'<option value="{escape(val, quote=True)}"'
            f'{" selected" if val == selected else ""}>{escape(lbl)}</option>'
            for val, lbl in zip(options, labels, strict=False)
        )
        return (
            f'<select class="w" data-wid="{widget_id}"{disabled} style="{css}" '
            f"onchange=\"send(this.dataset.wid,'change',this.value)\">{items}</select>"
        )

    if kind == "Chip":
        label = escape(text_value(props.get("text", "")))
        selected = " font-weight:700;" if props.get("selected") else ""
        return (
            f'<button class="w" data-wid="{widget_id}"{disabled} '
            f'style="border-radius:999px;{selected}{css}" '
            f"onclick=\"send(this.dataset.wid,'press','')\">{label}</button>"
        )

    if kind == "Badge":
        label = escape(text_value(props.get("text", "")))
        bg = _css_colour(text_value(props.get("background", "#3F51B5")))
        fg = _css_colour(text_value(props.get("color", "#FFFFFF")))
        return (
            f'<span data-wid="{widget_id}" style="display:inline-block;padding:2px 8px;'
            f'border-radius:999px;background:{bg};color:{fg};font-size:12px;{css}">'
            f"{label}</span>"
        )

    if kind == "Stepper":
        value = escape(text_value(props.get("value", 0)))
        return (
            f'<div data-wid="{widget_id}" style="display:flex;gap:8px;align-items:center;{css}">'
            f'<button class="w" style="width:auto"{disabled} '
            f"onclick=\"send(this.parentElement.dataset.wid,'decrement','')\">-</button>"
            f"<span>{value}</span>"
            f'<button class="w" style="width:auto"{disabled} '
            f"onclick=\"send(this.parentElement.dataset.wid,'increment','')\">+</button>"
            f"</div>"
        )

    if kind == "SearchBar":
        value = escape(text_value(props.get("value", "")), quote=True)
        placeholder = escape(text_value(props.get("placeholder", "")), quote=True)
        return (
            f'<input class="w" type="search" data-wid="{widget_id}" value="{value}" '
            f'placeholder="{placeholder}"{disabled} style="{css}" '
            f"oninput=\"send(this.dataset.wid,'change',this.value)\" "
            f"onkeydown=\"if(event.key==='Enter')send(this.dataset.wid,'search',this.value)\">"
        )

    if kind == "RadioButton":
        checked = " checked" if props.get("selected") else ""
        label = escape(text_value(props.get("text", "")))
        return (
            f'<label style="display:flex;gap:8px;align-items:center;{css}">'
            f'<input type="radio" data-wid="{widget_id}"{checked}{disabled} '
            f"onchange=\"send(this.dataset.wid,'press','')\">"
            f"<span>{label}</span></label>"
        )

    if kind == "SegmentedButtons":
        options = [str(o) for o in (props.get("options") or [])]
        labels = [str(lbl) for lbl in (props.get("labels") or options)]
        while len(labels) < len(options):
            labels.append(options[len(labels)])
        selected = text_value(props.get("value", ""))
        buttons = ""
        for val, lbl in zip(options, labels, strict=False):
            active = "font-weight:700;" if val == selected else ""
            buttons += (
                f'<button class="w" data-wid="{widget_id}"{disabled} '
                f'style="{active}" '
                f"onclick=\"send(this.dataset.wid,'change','{_js_value(val)}')\">"
                f"{escape(lbl)}</button>"
            )
        classes = "seg wrap" if props.get("wrap") else "seg"
        return f'<div class="{classes}" style="{css}">{buttons}</div>'

    if kind == "ProgressText":
        text = escape(text_value(props.get("text", "")))
        maximum = props.get("maximum", 100) or 100
        value = props.get("value", 0)
        return (
            f'<div data-wid="{widget_id}" style="{css}">'
            f'<progress class="w" max="{maximum}" value="{value}"></progress>'
            f'<div class="muted">{text}</div></div>'
        )

    if kind == "Link":
        label = escape(text_value(props.get("text", "")))
        url = escape(text_value(props.get("url", "")), quote=True)
        return (
            f'<a class="w" data-wid="{widget_id}" href="{url or "#"}" '
            f'style="{css}" '
            f"onclick=\"event.preventDefault();send(this.dataset.wid,'press','')\">{label}</a>"
        )

    if kind == "DataTable":
        headers = "".join(f"<th>{escape(str(h))}</th>" for h in (props.get("headers") or []))
        rows = "".join(
            "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
            for row in (props.get("rows") or [])
        )
        return (
            f'<table class="w" data-wid="{widget_id}" style="{css}">'
            f"<thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"
        )

    if kind == "Avatar":
        text = escape(text_value(props.get("text", "") or "?"))
        size = int(props.get("size", 48) or 48)
        bg = _css_colour(text_value(props.get("background", "#3F51B5")))
        fg = _css_colour(text_value(props.get("color", "#FFFFFF")))
        return (
            f'<div class="avatar" data-wid="{widget_id}" '
            f'style="width:{size}px;height:{size}px;background:{bg};color:{fg};{css}">'
            f"{text}</div>"
        )

    if kind == "ListTile":
        title = escape(text_value(props.get("title", "")))
        subtitle = escape(text_value(props.get("subtitle", "")))
        trailing = escape(text_value(props.get("trailing", "")))
        # A long press has no mouse equivalent, so the browser preview maps it
        # to the context menu (right click / touch-and-hold), which is what a
        # desktop tester reaches for anyway.
        long_press = (
            " oncontextmenu=\"send(this.dataset.wid,'long_press','');return false\""
            if props.get("long_pressable")
            else ""
        )
        tile = (
            f'<button class="w" data-wid="{widget_id}"{disabled} style="text-align:left;{css}" '
            f"onclick=\"send(this.dataset.wid,'press','')\"{long_press}>"
            f"<div>{title}</div>"
            f'<div class="muted">{subtitle}</div>'
            f'<div class="muted">{trailing}</div></button>'
        )
        if not (props.get("swipe_left") or props.get("swipe_right")):
            return tile
        # No finger to drag with: each swipe direction becomes a button.
        buttons = ""
        for direction, arrow in (("right", "⟶"), ("left", "⟵")):
            if props.get(f"swipe_{direction}"):
                colour = _css_colour(text_value(props.get(f"swipe_{direction}_color", "#757575")))
                buttons += (
                    f'<button class="w swipe" data-wid="{widget_id}"{disabled} '
                    f'title="swipe {direction}" style="background:{colour}" '
                    f"onclick=\"send(this.dataset.wid,'swipe','{direction}')\">{arrow}</button>"
                )
        return f'<div class="row" style="gap:4px;align-items:stretch">{tile}{buttons}</div>'

    if kind == "BottomNavigation":
        options = [str(o) for o in (props.get("options") or [])]
        labels = [str(lbl) for lbl in (props.get("labels") or options)]
        while len(labels) < len(options):
            labels.append(options[len(labels)])
        tabs = []
        for val, lbl in zip(options, labels, strict=False):
            active = val == props.get("value")
            look = "font-weight:700;background:#3F51B5;color:#fff;" if active else ""
            tabs.append(
                f'<button class="w" data-wid="{widget_id}"{disabled} '
                f'style="flex:1;border-radius:0;{look}" '
                f"onclick=\"send(this.dataset.wid,'change','{_js_value(val)}')\">"
                f"{escape(lbl)}</button>"
            )
        return f'<nav class="row" style="gap:0;{css}">{"".join(tabs)}</nav>'

    if kind == "Dialog":
        title = escape(text_value(props.get("title", "")))
        sheet = bool(props.get("sheet"))
        radius = "16px 16px 0 0" if sheet else "12px"
        margin = "24px 0 0" if sheet else "12px 0"
        head = f'<h4 style="margin:0 0 8px">{title}</h4>' if title else ""
        return (
            f'<section style="border:1px solid #b0bec5;border-radius:{radius};'
            f"padding:12px;margin:{margin};box-shadow:0 6px 18px rgba(0,0,0,.18);{css}\">"
            f"{head}{inner}</section>"
        )

    if kind == "DatePicker":
        value = escape(text_value(props.get("value", "")), quote=True)
        return (
            f'<input type="date" class="w" data-wid="{widget_id}"{disabled} '
            f'value="{value}" style="{css}" '
            f"onchange=\"send(this.dataset.wid,'change',this.value)\">"
        )

    if kind == "TimePicker":
        value = escape(text_value(props.get("value", "")), quote=True)
        return (
            f'<input type="time" class="w" data-wid="{widget_id}"{disabled} '
            f'value="{value}" style="{css}" '
            f"onchange=\"send(this.dataset.wid,'change',this.value)\">"
        )

    if kind in ("Icon", "IconButton", "AutoComplete", "RangeSlider", "PageView", "Chart"):
        return render_extra_html(node, render_html, css)

    return f'<div class="muted">&lt;{escape(kind)}&gt;</div>'


class WebPreview:
    """Serves an application to a browser and feeds interactions back.

    The preview runs an HTTP server that can read the app's state and trigger
    its widgets, so **where it listens** is a security decision:

    * the default is loopback (``127.0.0.1``) — reachable only from this
      machine, which is what a preview is for;
    * binding to another interface (``--host 0.0.0.0``) is an explicit opt-in
      and switches on the session token: every request must carry it, the page
      hands it to its own JavaScript, and requests from another origin are
      refused. Without that, anyone able to reach the port could read the
      screen and press its buttons.
    * a ``--host`` that is not the default prints a warning saying so.

    Unknown routes are refused (the handler used to accept a POST to *any*
    path), the body of an event is size-limited, and only ``application/json``
    is accepted for events.
    """

    def __init__(
        self,
        app: App,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        token: str | None = None,
    ) -> None:
        self.app = app
        self.host = host
        self.port = port
        self.exposed = not _is_loopback(host)
        #: Session token. Generated whenever the preview leaves loopback; a
        #: loopback preview does not need one and stays click-to-open.
        self.token = token or (secrets.token_urlsafe(18) if self.exposed else "")
        if self.token and token is None:
            _log.info("preview token generated for the exposed preview")
        self._tree: dict[str, Any] = {}
        self._status = ""
        self._version = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    # -- state -------------------------------------------------------------
    def update(self, tree: dict[str, Any]) -> None:
        """Publish a newly rendered tree to connected browsers."""
        with self._lock:
            self._tree = tree
            self._version += 1

    def toast(self, message: str) -> None:
        """Show a message in the status strip."""
        with self._lock:
            self._status = message
            self._version += 1

    def state(self) -> dict[str, Any]:
        """The payload the page polls for.

        ``chrome`` travels with every poll, not only with the initial page: a
        theme switched at runtime (``App.set_theme("dark")``) darkens the
        widgets the moment the next tree is rendered, and without the colours
        here the phone frame around them would stay light until a reload.
        """
        with self._lock:
            tree = self._tree
            status = self._status
            version = self._version
        return {
            "version": version,
            "title": str(tree.get("screen") or self.app.name),
            "body": render_html(tree) if tree else "",
            "depth": self.app.navigator.depth,
            "status": status,
            "snackbar": tree.get("snackbar") if tree else None,
            "chrome": self._theme_vars(),
        }

    def _theme_vars(self) -> dict[str, str]:
        """Chrome colours that follow ``App(theme=...)``."""
        theme = getattr(self.app, "theme", None)
        dark = bool(getattr(theme, "is_dark", False))
        colour = getattr(theme, "color", None)

        def pick(name: str, light: str, dark_value: str) -> str:
            if colour is not None:
                try:
                    return str(colour(name))
                except Exception:
                    pass
            return dark_value if dark else light

        return {
            "color_scheme": "dark" if dark else "light",
            "page_bg": "#121212" if dark else "#eceff1",
            "phone_bg": pick("BACKGROUND", "#fff", "#121212"),
            "bar_bg": pick("SURFACE", "#f7f8fa", "#1e1e1e"),
            "text": pick("TEXT", "#212121", "#EEEEEE"),
            "muted": pick("TEXT_MUTED", "#607d8b", "#9e9e9e"),
            "primary": pick("PRIMARY", "#3F51B5", "#9fa8da"),
            "line": "#333" if dark else "#e3e7ea",
            "snack_bg": "#e6e6e6" if dark else "#323232",
            "snack_fg": "#212121" if dark else "#ffffff",
            "snack_action": pick("PRIMARY", "#8c9eff", "#3F51B5") if dark else "#8c9eff",
        }

    def page(self) -> str:
        """The full HTML document."""
        state = self.state()
        return _PAGE.format(
            title=escape(state["title"]),
            body=state["body"],
            version=state["version"],
            # json.dumps, not repr: the value is going inside JavaScript.
            token_literal=json.dumps(self.token),
            extra_script=EXTRA_SCRIPT,
            **self._theme_vars(),
        )

    def dispatch(self, widget_id: str, kind: str, value: str) -> None:
        """Apply a browser interaction to the application."""
        try:
            self.app.handle_ui_event(widget_id, kind, value)
        except Exception as error:  # pragma: no cover - user callback failed
            _log.exception("handler for %s failed", widget_id)
            self.toast(f"{type(error).__name__}: {error}")

    # -- server ------------------------------------------------------------
    def serve_forever(self) -> None:
        """Run the HTTP server until interrupted."""
        server = self._build_server()
        _log.info("serving on %s", browser_url(self.host, server.server_port))
        try:
            server.serve_forever()
        except KeyboardInterrupt:  # pragma: no cover - interactive
            pass
        finally:
            server.shutdown()
            server.server_close()

    def start_background(self) -> int:
        """Start serving on a daemon thread and return the bound port."""
        server = self._build_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return int(server.server_port)

    def stop(self) -> None:
        """Shut the server down."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _route(self, raw_path: str) -> tuple[str, dict[str, list[str]]]:
        """Split a request target into (path, query)."""
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(raw_path)
        return parts.path.rstrip("/") or "/", parse_qs(parts.query)

    def _authorised(self, path: str, query: dict[str, list[str]], headers: Any) -> bool:
        """Whether a request may be served.

        A loopback preview is private and needs no token (a local tool, a
        test). An exposed one requires the token, in the query string (that is
        how the browser gets it from the printed URL) or in ``X-PMB-Token``
        (scripts). A request that states a different ``Origin`` is refused
        even with a valid token: a page on another site must not be able to
        drive this app through the user's browser.
        """
        if not self.token:
            return True
        origin = headers.get("Origin")
        if origin:
            # П-07: accept both http:// and https:// origins. When a TLS-
            # terminating proxy (Cloudflare tunnel, SSH port-forward, nginx,
            # corporate portal) sits in front it rewrites the scheme; the
            # browser still sends Origin with the public https:// URL. The
            # host header (or X-Forwarded-Host when set) must still match.
            from urllib.parse import urlsplit

            try:
                origin_parts = urlsplit(origin.rstrip("/"))
                origin_host = origin_parts.netloc
                origin_scheme = origin_parts.scheme
            except ValueError:
                _log.debug("refusing a request from malformed origin %s", origin)
                return False
            if origin_scheme not in ("http", "https"):
                _log.debug("refusing a request from origin with scheme %s", origin_scheme)
                return False
            host = (
                headers.get("X-Forwarded-Host")
                or headers.get("X-Forwarded-Server")
                or headers.get("Host", "")
            ).split(",")[0].strip()
            # Host header sometimes carries :port — compare host and port
            # against the origin host which may include a port.
            if origin_host != host:
                _log.debug("refusing a request from origin %s (host %s)", origin, host)
                return False
        supplied = (query.get("t") or [""])[0] or headers.get("X-PMB-Token", "")
        if not secrets.compare_digest(supplied, self.token):
            return False
        return path in ("/", "/state", "/event")

    def _build_server(self) -> ThreadingHTTPServer:
        preview = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                """Silence the default per-request stderr logging."""

            def _reply(
                self,
                body: bytes,
                content_type: str,
                status: int = 200,
                extra: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                # A preview must never be embedded by another page.
                self.send_header("X-Frame-Options", "DENY")
                for name, value in (extra or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _discard_body(self) -> None:
                """Consume a body the handler is not going to use.

                Rejecting a request without reading its body leaves those bytes
                in the socket; the client is still sending and loses the status
                line to a connection reset (WinError 10053 on Windows). Reading
                them first makes the error visible, and closing after the reply
                keeps a half-read request from being parsed as a new one.
                """
                if self.command in ("GET", "HEAD"):
                    self.close_connection = True
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > 0:
                    _drain_body(self.rfile, length)
                self.close_connection = True

            def _reject(self, body: bytes, status: int) -> None:
                """Answer with an error, having first consumed the body."""
                self._discard_body()
                self._reply(body, "text/plain; charset=utf-8", status=status)

            def _forbidden(self) -> None:
                message = (
                    "Refused: this preview was started with a session token.\n"
                    "Open the URL printed by `pymobile run --web` (it carries "
                    "?t=…), or pass the token in the X-PMB-Token header.\n"
                ).encode()
                self._reply(message, "text/plain; charset=utf-8", status=403)

            def _not_found(self, path: str) -> None:
                body = f"No such route: {path}\n".encode()
                self._reply(body, "text/plain; charset=utf-8", status=404)

            def do_GET(self) -> None:  # required name of the http.server API
                path, query = preview._route(self.path)
                if path not in ("/", "/state"):
                    self._not_found(path)
                    return
                if not preview._authorised(path, query, self.headers):
                    self._forbidden()
                    return
                if path == "/state":
                    payload = json.dumps(preview.state()).encode("utf-8")
                    self._reply(payload, "application/json; charset=utf-8")
                    return
                self._reply(preview.page().encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self) -> None:  # required name of the http.server API
                path, query = preview._route(self.path)
                # The handler used to accept a POST to *any* path and run the
                # event it carried. Only /event is an event.
                if path != "/event":
                    self._discard_body()
                    self._not_found(path)
                    return
                if not preview._authorised(path, query, self.headers):
                    self._discard_body()
                    self._forbidden()
                    return
                content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if content_type and content_type != "application/json":
                    self._reject(b"Content-Type must be application/json\n", 415)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_EVENT_BYTES:
                    self._reject(
                        f"event body larger than {MAX_EVENT_BYTES} bytes\n".encode(), 413
                    )
                    return
                try:
                    event = json.loads(self.rfile.read(length) or b"{}")
                except (json.JSONDecodeError, ValueError):
                    self._reply(
                        b"event body is not valid JSON\n",
                        "text/plain; charset=utf-8",
                        status=400,
                    )
                    return
                if not isinstance(event, dict):
                    self._reply(
                        b"event body must be a JSON object\n",
                        "text/plain; charset=utf-8",
                        status=400,
                    )
                    return
                preview.dispatch(
                    str(event.get("id", "")),
                    str(event.get("kind", "")),
                    str(event.get("value", "")),
                )
                payload = json.dumps(preview.state()).encode("utf-8")
                self._reply(payload, "application/json; charset=utf-8")

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        server.daemon_threads = True
        self._server = server
        self.port = int(server.server_port)
        return server


def serve(app: App, *, host: str = "127.0.0.1", port: int = 8765) -> WebPreview:
    """Serve ``app`` in a browser, blocking until interrupted.

    Binds loopback by default; pass ``host="0.0.0.0"`` explicitly to serve the
    LAN (the preview then requires its session token).
    """
    preview = WebPreview(app, host=host, port=port)
    preview.serve_forever()
    return preview
