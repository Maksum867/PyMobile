<div align="center">

# PyMobile

**Build real Android apps in pure Python.**

Write a declarative UI, run one command, install the APK on your phone.

[![PyPI](https://img.shields.io/pypi/v/pymobile-framework.svg)](https://pypi.org/project/pymobile-framework/)
[![Python](https://img.shields.io/pypi/pyversions/pymobile-framework.svg)](https://pypi.org/project/pymobile-framework/)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](https://github.com/Maksum867/py-mobile/issues)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/Maksum867/py-mobile/blob/main/LICENSE)

<p align="center">
  <img src="https://raw.githubusercontent.com/Maksum867/py-mobile/main/docs/demo.gif"
       alt="PyMobile demo: from pip install to a native Android app" width="800">
</p>

</div>

> ### ⚠️ Alpha software — known bugs, actively being fixed
>
> PyMobile is at **0.9.x** and is still in alpha. It builds and signs real,
> installable APKs today, but the API can change between minor releases and
> there are known bugs. I am actively working on them: fixes ship in
> every release — see the
> [changelog](https://github.com/Maksum867/py-mobile/blob/main/CHANGELOG.md)
> for what has already landed and
> [Issues](https://github.com/Maksum867/py-mobile/issues) for what is still
> open.
>
> Good fit for personal apps, internal tools, prototypes and learning. If you
> depend on it, pin an exact version (`pymobile-framework==0.9.2`) and read the
> changelog before upgrading. What may change, and how much notice you get, is
> written down in the
> [versioning and deprecation policy](#versioning-and-deprecation-policy). Bug
> reports are genuinely welcome.

---

```python
from pymobile import App, Button, Column, Label, Screen, Widget


class Home(Screen):
    title = "Hello"

    def __init__(self) -> None:
        super().__init__()
        self.taps = 0

    def build(self) -> Widget:
        self.counter = Label("Taps: 0")
        return Column(
            Label("Hello, Android!"),
            self.counter,
            Button("Tap me", on_press=self.on_tap),
            spacing=12,
        )

    def on_tap(self) -> None:
        self.taps += 1
        self.counter.text = f"Taps: {self.taps}"      # the screen redraws itself


App("Demo").run(Home())
```

```bash
pip install pymobile-framework
pymobile setup-sdk           # once per machine
pymobile build --native      # → build/demo-0.1.0.apk
```

That APK is signed, installable and runs a real CPython interpreter on the
device. No Java, no Gradle, no Android Studio.

---

## Table of contents

- [Why PyMobile](#why-pymobile)
- [Installation](#installation)
- [Your first app](#your-first-app)
- [How it works](#how-it-works)
- [UI components](#ui-components)
- [Layout](#layout)
- [Styling](#styling)
- [Screens and navigation](#screens-and-navigation)
- [Updating the screen](#updating-the-screen)
- [Threads and the UI](#threads-and-the-ui)
- [Themes](#themes)
- [Storage](#storage)
- [Background jobs](#background-jobs)
- [HTTP cache and offline mode](#http-cache-and-offline-mode)
- [Async HTTP](#async-http)
- [Input validation](#input-validation)
- [Plugins](#plugins)
- [Notifications](#notifications)
- [Vibration](#vibration)
- [Permissions](#permissions)
- [HTTP requests](#http-requests)
- [Languages](#languages)
- [Events](#events)
- [Timers](#timers)
- [Configuration reference](#configuration-reference)
- [Building an APK](#building-an-apk)
- [Application icon](#application-icon)
- [Installing on a phone](#installing-on-a-phone)
- [Debugging](#debugging)
- [Testing your app](#testing-your-app)
- [Error handling](#error-handling)
- [Desktop preview](#desktop-preview)
- [CLI reference](#cli-reference)
- [Extending the framework](#extending-the-framework)
- [Limitations](#limitations)
- [Known issues](#known-issues)
- [Versioning and deprecation policy](#versioning-and-deprecation-policy)
- [FAQ](#faq)
- [Support the project](#support-the-project)
- [Contributing](#contributing)

---

## Why PyMobile

| | |
| --- | --- |
| **Real APKs** | Signed with v2+v3 schemes, containing `classes.dex`, `resources.arsc` and an embedded CPython 3.14 for ARM64. |
| **Native views** | Widgets become genuine `TextView`, `Button`, `EditText`, `Switch`, `ProgressBar` and `LinearLayout` instances — not a web view or a custom canvas. |
| **Fast** | About five seconds per build. An unchanged rebuild is instant. |
| **Small setup** | ~800 MB of tooling, downloaded automatically. The NDK is not required. |
| **Testable** | Your whole app runs and is unit-testable on a desktop, with no emulator. |
| **No dependencies** | Nothing but the standard library runs on the device, which keeps APKs small. The desktop package pulls in `certifi` only to bundle CA certificates at build time. |

Verified on a physical device running Android 14.

---

## Installation

**Requirements:** Python 3.10+, about 1 GB of free disk space, an Android 7.0+
(API 24) device.

```bash
pip install pymobile-framework
```

> The distribution is named **`pymobile-framework`** because `pymobile` on PyPI belongs
> to an unrelated 2014 project. The import name and the CLI command are still
> `pymobile`. The GitHub repository is `py-mobile`.

Then install the Android toolchain, once per machine:

```bash
pymobile setup-sdk
```

This downloads JDK 17 and the Android SDK (~800 MB) into `~/.andro`. The NDK is
**not** downloaded: a prebuilt native bridge (22 KB) ships inside the package.
It is byte-identical for every application, so there is nothing to compile. If
you want to rebuild it from C source anyway:

```bash
pymobile setup-sdk --with-ndk    # adds ~2 GB
```

Already have Android Studio? Point PyMobile at your existing SDK instead:

```bash
export ANDROID_HOME=~/Android/Sdk          # macOS/Linux
$env:ANDROID_HOME = "C:\...\Android\Sdk"   # Windows PowerShell
```

You need `build-tools;35.0.0` and `platforms;android-35`.

### If the `pymobile` command is not found

Common on Windows when pip's scripts directory is not on `PATH`. This form
always works:

```bash
python -m pymobile build --native
```

Verify everything with:

```bash
pymobile doctor
```

---

## Your first app

```bash
pymobile init myapp --name "My App" -p com.example.myapp
cd myapp
pymobile run                # preview on your machine
pymobile build --native     # produce the APK
```

`init` generates:

```
myapp/
├── main.py           # your application
├── pymobile.toml     # configuration
├── README.md
├── .gitignore
└── assets/           # images, fonts, data files
```

The smallest possible app:

```python
from pymobile import App, Column, Label, Screen, Widget


class Home(Screen):
    def build(self) -> Widget:
        return Column(Label("Hello, Android!"))


App("My App").run(Home())
```

---

## How it works

Three ideas explain everything else.

**1. Widgets are descriptions, not views.** `Label("Hello")` draws nothing. It
is a node in a tree that serialises to JSON. A Java renderer converts that tree
into real Android views and patches them in place on every update.

**2. Everything platform-specific goes through a bridge.**

```
Your Python code
       ↓
    Bridge ──┬── AndroidBridge   on device, through JNI
             └── StubBridge      on desktop, records every call
```

**3. Therefore your app runs anywhere.** On a laptop the stub bridge logs what
would have happened; on a phone the same code vibrates and posts notifications.
You never write `if android:` — the framework picks the implementation.

```python
app = App("Demo")
print(app.platform)      # 'desktop' or 'android'
print(app.bridge.name)   # 'stub' or 'android'
```

**The build pipeline** runs twelve independent, individually timed stages:

```
validate → collect → compile → icons → toolchain → runtime
        → jni → dex → resources → assets → sign → verify
```

---

## UI components

30+ components. All of them accept `id`, `style`, `visible` and `enabled`.

### Label

Static or updatable text.

```python
label = Label("Heading", style=Style(font_size=24, bold=True))
label.set_text("New text")
```

### Button

```python
Button("Save", on_press=self.save)
Button("Unavailable", on_press=self.save, enabled=False)   # taps ignored
```

### TextInput

```python
TextInput(
    placeholder="Your name",
    max_length=50,             # extra characters are trimmed automatically
    on_change=lambda value: print(value),
)

TextInput(password=True)       # masked
TextInput(multiline=True)      # multi-line
```

`on_change` fires only on a genuine change — writing the same value again does
nothing. While the field has focus the framework never overwrites its contents,
so the keyboard stays open and the caret does not jump.

Methods: `set_value(text)`, `clear()`.

> `maxlength=` (the HTML spelling) still works but is **deprecated** in favour
> of `max_length=`; see [Versioning and deprecation policy](#versioning-and-deprecation-policy).

### Switch

```python
switch = Switch(checked=True, on_toggle=lambda state: print(state))
switch.toggle()            # returns the new state
switch.set_checked(False)
```

### Checkbox

```python
checkbox = Checkbox(checked=False, on_toggle=lambda state: print(state))
checkbox.toggle()
checkbox.set_checked(True)
```

### RadioButton / RadioGroup

```python
group = RadioGroup(
    RadioButton("Option A"),
    RadioButton("Option B"),
    on_select=lambda value: print(f"Selected: {value}"),
)
group.value              # "Option A" / None — the selected button's text
group.selected_index     # 0 / None — position, works with duplicate labels
group.select("Option B") # by text (the first button with that text)
group.select_index(1)    # by position
```

Buttons are tracked by position, so two options may share a label
(`"Other"`, `"Other"`) and a label may be changed at run time.

### SegmentedButtons

Horizontal selection panel (tabs or segmented control).

```python
segmented = SegmentedButtons(
    ["Today", "Week", "Month"],
    on_select=lambda value: print(value),
)
```

**Long labels.** Every segment is as wide as its text. When they do not fit —
five translated labels on a 360 dp phone — the bar **scrolls sideways** (the
selected segment is scrolled into view) instead of squeezing a word into a
column of letters. Pass `wrap=True` when every option should stay visible: the
segments then flow onto further lines.

```python
SegmentedButtons(["Географія", "Історія", "Математика", "Біологія", "Хімія"])            # scrolls
SegmentedButtons(["Географія", "Історія", "Математика", "Біологія", "Хімія"], wrap=True)  # flows
```

For anything other than a selection — tags, filter chips, quick replies — use
[`Wrap`](#wrap).

### Slider

```python
slider = Slider(value=50, minimum=0, maximum=100,
                on_change=lambda v: print(v))
```

`min=` / `max=` still work as **deprecated** aliases of `minimum=` / `maximum=`
(on `Slider`, `Stepper`, `ProgressBar`, `ProgressText` and `RatingBar`); they
warn and are removed in 1.0.

### ProgressBar

```python
bar = ProgressBar(40, maximum=100)
bar.set_value(150)         # clamped to maximum
bar.fraction               # 0.0 … 1.0

ProgressBar(indeterminate=True)    # spinner
```

### ProgressText

ProgressBar with a text label overlay.

```python
ProgressText(42, maximum=100, label="Downloading")
# Shows: "Downloading 42%"
```

### RatingBar

```python
rating = RatingBar(value=3, maximum=5,
                   on_change=lambda v: print(v))
```

### Dropdown

```python
dropdown = Dropdown(
    options=["Red", "Green", "Blue"],
    on_select=lambda value: print(value),
)
```

Widgets that *choose one of several options* (`Dropdown`, `SegmentedButtons`,
`BottomNavigation`, `RadioGroup`) report it with `on_select`; widgets that hold
a *value* (`TextInput`, `Slider`, `Stepper`, `RatingBar`) use `on_change`. The
old `on_change=` on the selection widgets is a **deprecated** alias of
`on_select=`.

### SearchBar

```python
search = SearchBar(
    placeholder="Search...",
    on_change=lambda query: filter_items(query),
)
```

### Chip / Badge

```python
Chip("Python", on_press=lambda: print("clicked"))
Badge("3")       # notification badge
```

### Stepper

```python
stepper = Stepper(value=1, minimum=0, maximum=10,
                  on_change=lambda v: print(v))
```

### Link

Clickable text that opens a URL in the system browser.

```python
Link("Visit example.com", url="https://example.com")
```

### DataTable

Simple table with headers and rows.

```python
DataTable(
    headers=["Name", "Age", "City"],
    rows=[
        ["Alice", "30", "Kyiv"],
        ["Bob", "25", "Lviv"],
    ],
)
```

### Avatar

Round avatar with initials or an image.

```python
Avatar("MK")                           # initials
Avatar("MK", image="assets/photo.png") # image, showing "MK" until it loads
```

The positional argument is *either* initials *or* an image source, and `Avatar`
guesses: a URL, a path separator or an image extension means "image",
everything else is initials. State the reading outright whenever the guess
would be wrong:

```python
Avatar(text="A/B")                    # initials, whatever they look like
Avatar(image="photo")                 # an image, whatever the path looks like
Avatar("A/B", is_image=False)         # the positional is initials, not a path
Avatar("photo", is_image=True)        # the positional is an image source
Avatar("A/B", image="me.png", is_image=False)   # a photo with the initials "A/B"
```

`is_image` is three-valued: `None` (the default) guesses, `True` and `False`
decide.

### Image

```python
Image("assets/logo.png", fit="cover")   # contain | cover | fill | none
```

Paths are relative to your project directory.

### List / ListTile

A list that builds its rows lazily, a page at a time, so 10 000 items cost only
what the user actually scrolls through.

```python
List(
    item_count=10000,
    builder=lambda index: ListTile(
        title=f"Item {index}",
        subtitle=f"Description {index}",
        on_press=lambda: print(f"tapped {index}"),
        on_long_press=lambda: delete(index),   # touch and hold
    ),
)
```

The first `visible_count` rows (default 20) are built up front. When the last
built row scrolls into view, the next page is appended in place — the scroll
position is kept. The Tk and browser previews show a *Load more (N of M)*
button instead. Rows stay built once loaded: this is incremental loading, not
view recycling, so keep rows light.

```python
items = List(item_count=10000, builder=row, visible_count=30)
items.loaded        # 30 rows built so far
items.has_more      # True
items.load_more()   # append the next page yourself (returns rows added)
items.scroll_to(250)            # build rows up to 250 and scroll to it
items.item_count = 12000; items.refresh()   # data changed
```

`scroll_to(index, animated=True)` really scrolls: the enclosing `ScrollView`
brings row `index` to the top, smoothly unless `animated=False`. Calling it
again with the same index scrolls again (the user may have scrolled away).
The browser preview scrolls the row into view.

`on_long_press` gives a row a second action — delete, archive, rename —
without a permanent button on every row. The device vibrates on the hold; the
Tk and browser previews map it to a right click.

#### Swipe and pull to refresh

```python
def build(self):
    return ScrollView(
        List(
            len(self.mails),
            builder=lambda i: ListTile(
                self.mails[i].subject,
                on_swipe_left=lambda: self.delete(i),     # red, like a bin
                on_swipe_right=lambda: self.archive(i),   # green
            ),
            on_refresh=self.reload,
        )
    )

def reload(self):
    return self.app.run_job(fetch_mail).then(self.show)   # spins until done
```

- **Swipe.** Dragging a row sideways tints it (`swipe_left_color` /
  `swipe_right_color`, red and green by default). Let go past a third of the
  width — or fling it — and the row slides out and the handler runs; a short
  drag springs back. Only the directions that have a handler move. In tests,
  `tile.swipe("left")` does what the gesture does.
- **Pull to refresh.** Pulling down a list that is scrolled to the top shows
  a spinner and calls `on_refresh`. If it returns something with `then()` (a
  `run_job` handle, an `app.http` future), the spinner keeps turning until
  that finishes, successfully or not; otherwise it stops when the handler
  returns (a cancelled job never finishes: set `refreshing = False` then).
  Set `items.refreshing = True/False` to drive the spinner yourself;
  `items.pull_to_refresh()` simulates the gesture.
- The Tk and browser previews have no finger: a swipe becomes ⟵ / ⟶ buttons
  next to the row and the pull a *↻ Pull to refresh* button.

Both gestures are implemented in the renderer itself — no AndroidX, nothing
extra in the APK.

### BottomNavigation

The app's primary navigation — a persistent bar of equal-width tabs
(Android's bottom-navigation pattern). Unlike `SegmentedButtons` (a content
filter), renderers pin it: bottom bar in the browser preview, tab row in the
Tk window, horizontal tab bar on the device.

```python
tabs = BottomNavigation(
    ["Home", "Stats", "Settings"],
    value="Home",
    on_select=self.show_tab,      # fires only on a real change
)
tabs.select("Stats")              # programmatic switch redraws the bar
```

### Dialog, AlertDialog, ConfirmDialog, BottomSheet

Modal surfaces drawn as a framed, elevated card (a `LabelFrame` in Tk, a
bordered `<section>` in the browser, a card with elevation on Android).
`open()`/`close()` flip `visible`, so a closed dialog costs nothing::

```python
self.ask = ConfirmDialog(
    "Delete entry?", "This cannot be undone.",
    on_confirm=self.delete, confirm_text="Delete",
)
...
self.ask.open()                   # shown = True, the screen redraws
```

Whichever button the user taps closes the dialog **first** and only then
fires `on_confirm`/`on_cancel`/`on_acknowledge`, so a handler never runs
while the dialog still covers the screen. `AlertDialog` is a message plus one
button; `BottomSheet(title=...)` anchors the same surface to the bottom edge
with rounded top corners.

### Snackbar

A message at the bottom of the screen with an optional action — the classic
*"Note deleted · UNDO"*:

```python
def delete(self, index):
    note = self.notes.pop(index)
    self.refresh()
    self.app.snackbar(
        "Note deleted",
        action="Undo",
        on_action=lambda: self.restore(index, note),
    )
```

It hides itself after 4 s (7 s when it has an action; `duration_ms=0` keeps it
until tapped or `dismiss()`ed), can be swiped away, and a new snackbar
replaces the one on screen. It stays up across `push`/`pop`. `app.snackbar()`
returns the `Snackbar`; `app.current_snackbar` is the one on screen (or
`None`), so tests can check it and call `.press()` / `.dismiss()`. Unlike
`toast()` it is drawn by the framework, so the previews and the PNG mockup
show it too.

### DatePicker / TimePicker

ISO-string values (`"2026-09-19"`, `"14:30"`) that serialise to JSON untouched.
The browser preview renders real `<input type="date">` / `<input type="time">`
controls, the device opens the native `DatePickerDialog` / `TimePickerDialog`.
Out-of-range values clamp to `minimum`/`maximum`; malformed ones raise
`ValueError` immediately.

```python
DatePicker(value="2026-09-19", minimum="2026-01-01",
           on_change=lambda iso: print(iso))
TimePicker(value="14:30", on_change=self.on_time)
```

### Cards, forms, tabs and other extended components

These components are exported from `pymobile` like the basic ones. Cards, forms,
tabs, accordions, states, multi-select, calendars and the carousel are *compositions*
of existing widget types (`Column`, `Row`, `Grid`, `Wrap`, `Button`, `Label`, `Chip`),
so they need no new Java. `Icon`, `IconButton`, `AutoComplete`, `RangeSlider`,
`PageView` and the charts are native types and live in `ViewBuilder.java` /
`AdvancedViews.java` (the bundled `classes.dex` already contains them).

```python
Card(Label("Body"), title="Profile", actions=[Button("Edit")])

Form(
    FormField("email", "Email", required=True, rules=["email"]),
    FormField("password", "Password", password=True, rules=[{"min_length": 8}]),
    on_submit=lambda values: print(values),
    messages={"required": "обов'язкове поле"},   # same keys as Validator(messages=)
)                                                # call form.submit() from a Button

TabView({"home": HomeView(), "stats": lambda: StatsView()}, labels={"home": "Головна"})
ExpansionPanel("Details", Label("…")); Accordion(panel_a, panel_b)   # one open at a time
MultiSelect({"a": "Alpha", "b": "Beta"}, ["a"], maximum_selected=2)
AutoComplete(["Kyiv", "Lviv"], threshold=2, on_select=print)
RangeSlider(20, 70, minimum=0, maximum=100, step=5, on_change=print)   # value == (low, high)
IconButton("add", label="Add item", on_press=self.add)    # label is required for TalkBack
PageView(page_a, page_b)   # or PageView(item_count=100, builder=make_page)
Carousel(page_a, page_b)   # PageView + buttons + "1 / 2" indicator
Calendar("2026-10-01"); DateRangePicker(on_change=print)   # ISO dates
BarChart({"Jan": 4, "Feb": -2}); LineChart([1, 3, 2]); PieChart({"A": 3, "B": 1})
EmptyState("Nothing here", action_label="Add", on_action=self.add); Skeleton(3)
```

Things worth knowing:

- Keep stateful ones (`Form`, `TabView`, `PageView`, `MultiSelect`, `Calendar` …) in
  `__init__` or on `self`, not created fresh inside `build()`, when their state must
  survive `Screen.refresh()`. They repair their own parent links after a refresh.
- Disabling a `Form`, `TabView`, `Card`… (`enabled=False`) blocks events for every
  widget inside it, not only for the container.
- `TabView` is not a nested `Screen`: the owning screen renders the active content.
  A builder (`lambda: …`) creates fresh content on selection; keep the data outside.
- `Icon` names are in `pymobile.ICON_NAMES`; they are drawn as vectors, not emoji.
  `IconButton` must be at least 48 dp and have a non-empty `label`.
- `RangeSlider` snaps to `step` from `minimum`; the phone commits the value when the
  finger is lifted. `PageView` renders only the selected page.
- Charts are read-only, equally spaced, and support signed values. `PieChart` rejects
  negative values. Large inputs are labelled sparsely instead of crowding the axis.
- Remote suggestions for `AutoComplete`: run the request in a job and call
  `set_options(...)`; debouncing belongs to the application.

### Spacer

```python
Spacer(16)     # 16 dp of empty space
```

### Divider

```python
Divider()                       # hairline between sections
Divider(inset=16, thickness=2)
```

---

## Layout

Containers, freely nestable.

```python
Column(a, b, c, spacing=12)                # vertical stack
Row(a, b, spacing=8, align=Align.CENTER)   # horizontal stack
Grid(a, b, c, d, columns=2, spacing=12)    # equal-width cells
Wrap(a, b, c, d, spacing=8)                # side by side, a new line when full
ScrollView(content, spacing=8)             # scrollable region
Stack(background, foreground)              # layered, last on top
SafeArea(content)                          # clears the notch and status bar
```

### Grid

The container for cards, galleries and menus. Every cell in a column gets the
same width, so two stat cards stay aligned even when one holds `5` and the
other `2 h 45 m` — which is exactly what `Row(weight=1)` cannot guarantee.

```python
Grid(
    card("Completed", "12"),  card("Focus time", "5 h"),
    card("Breaks", "4"),      card("Average", "25 m"),
    columns=2,
    spacing=12,                       # or row_spacing= / column_spacing=
)
```

Rows fill left to right; the last row may be partially filled.

### Wrap

A flow layout: children sit side by side at their natural size, and a new line
starts when the next one does not fit. It is the container for things whose
number and width are not known in advance — tags, filter chips, quick-reply
buttons, translated labels — where a `Row` would push the last one off the
screen and a `Grid` would give every cell the width of the widest.

```python
Wrap(*[Chip(tag) for tag in tags], spacing=8, run_spacing=4)
Wrap(a, b, c, align="center")     # each line centred (start | center | end)
```

`spacing` is the gap between children on a line, `run_spacing` the gap between
lines (it defaults to `spacing`). A child wider than the container gets a line
of its own; hidden children take no room. The Android renderer, the desktop
preview and the mockup break lines by the same rule.

### Expanded and Flexible

Share out the space left over on the main axis.

```python
Row(
    Expanded(Button("Start")),            # half
    Expanded(Button("Reset"), flex=2),    # twice as wide
    spacing=8,
)
```

`Expanded` makes the child fill its share, `Flexible` lets it stay smaller.

### Divider

```python
Column(
    header,
    Divider(),                                   # hairline
    Divider(inset=16, thickness=2),              # inset, thicker
    Divider(vertical=True),                      # inside a Row
    body,
)
```

### SafeArea

```python
def build(self) -> Widget:
    return SafeArea(Column(...))                 # all edges
    return SafeArea(content, top=False)          # header bleeds upwards
```

Padding comes from the real window insets, so it is right on every device
instead of a hard-coded guess.

### Alignment

`align` positions children along the container's own axis; `cross_align`
positions them across it.

```python
Row(a, b, align=Align.SPACE_BETWEEN, cross_align=Align.CENTER)
Column(a, b, cross_align=Align.STRETCH)
```

Options: `Align.START`, `Align.CENTER`, `Align.END`, `Align.SPACE_BETWEEN`,
and `Align.STRETCH` for `cross_align`.

---

## Styling

```python
Style(
    font_size=18,
    bold=True,
    italic=False,
    color=Color.PRIMARY,
    background=Color.SURFACE,
    padding=EdgeInsets.all(16),
    margin=EdgeInsets.symmetric(horizontal=8, vertical=4),
    corner_radius=8,
    elevation=2,
    align=Align.CENTER,
)
```

`padding` and `margin` also take shorthands: `padding=16` is
`EdgeInsets.all(16)` and `padding=(16, 8, 16, 8)` is left, top, right, bottom
(the `EdgeInsets` order). A pair such as `(8, 16)` raises a `TypeError` —
CSS and `EdgeInsets.symmetric` read it in opposite orders, so write
`EdgeInsets.symmetric(horizontal=16, vertical=8)` instead.

### Spacing between specific neighbours

`spacing` on a container applies the same gap everywhere. When one gap must
differ, add a `margin` to the individual widget — the two are **added**, not
replaced:

```python
Column(
    header,
    Label("close to the header"),
    Label("pushed further down", style=Style(margin=EdgeInsets(top=24))),
    spacing=8,          # this one sits 8 + 24 dp below its neighbour
)
```

### Size constraints

```python
Style(min_width=120, max_width=200)     # a card that cannot collapse or sprawl
Style(aspect_ratio=16 / 9)              # keep a shape whatever the width
Style(min_height=44)                    # a comfortable tap target
Style(weight=1)                         # share of a Row/Column (see Expanded)
Style(width=120, height=48)             # a fixed size in dp
Style(width="match")                    # fill the parent ("wrap" also works)
Style(elevation=4)                      # a drop shadow (Android 5+)
```

Contradictory bounds raise immediately — `Style(min_width=200, max_width=120)`
is a `ValueError`, not a widget that silently clips to nothing on device.

Built-in colours: `PRIMARY`, `ACCENT`, `BACKGROUND`, `SURFACE`, `TEXT`,
`TEXT_MUTED`, `SUCCESS`, `WARNING`, `ERROR`, `TRANSPARENT`. Custom colours use
`#RGB`, `#RRGGBB` or `#AARRGGBB`.

Styles are immutable, so a base style is safe to share:

```python
BASE = Style(font_size=16, color=Color.TEXT)

Label("Normal", style=BASE)
Label("Error", style=BASE.merge(color=Color.ERROR, bold=True))
```

Invalid values fail fast: `Style(color="red")` raises `ValueError`.

`EdgeInsets` helpers: `EdgeInsets.all(8)`,
`EdgeInsets.symmetric(horizontal=8, vertical=4)`,
`EdgeInsets(left, top, right, bottom)`.

---

## Screens and navigation

A screen builds a widget tree and receives lifecycle callbacks.

```python
class Settings(Screen):
    title = "Settings"

    def build(self) -> Widget:
        return Column(
            Label("Second screen"),
            Button("Back", on_press=self.app.pop),
        )

    def on_mount(self):   ...   # once, when pushed onto the stack
    def on_show(self):    ...   # every time it becomes visible
    def on_hide(self):    ...   # when another screen covers it
    def on_unmount(self): ...   # once, when popped
```

Navigation is a stack:

```python
app.push(Settings())            # on_hide(current) → on_mount → on_show
app.pop()                       # on_hide → on_unmount → on_show(previous)
app.pop()                       # safe to call multiple times, returns None on root

app.replace(HomeScreen())       # after login, no back to login (App shortcut)
app.reset(LoginScreen())        # on logout, clear stack (App shortcut)

app.navigator.replace(Other())  # swap the top screen (via navigator)
app.navigator.reset(Home())     # clear the stack, start fresh
app.navigator.depth             # how many screens are stacked
app.screen                      # the visible screen
```

`build()` is now called **before** `on_mount()`/`on_show()`, so you can safely
access widgets created in `build()` from lifecycle hooks. `push(None)` and
`build()` returning `None` now raise clear `TypeError`/`PyMobileError` instead
of raw `AttributeError`.

The root screen is never popped — `pop()` returns `None` instead of leaving a
blank window. The hardware back button is wired up automatically.

> Do not push the same screen **object** twice; that raises `PyMobileError`.
> Create a new instance each time: `app.push(Settings())`.

### Is this screen on display?

`screen.is_current` is `True` while the screen is on top of the stack — from just
before `on_show` until just before `on_hide` — and `False` when another screen
covers it, after it was popped, or before it was pushed. It never raises and
can be read from any thread, so a callback that may outlive the screen (a
timer, a job result) can simply ask, without touching the private `_app`:

```python
def on_rows(self, rows):
    if self.is_current:               # same as `self.app.screen is self`, but never raises
        self.table.rows = rows
```

Not to be confused with `screen.mounted`, which stays `True` for a screen that
is on the stack but covered.

### Titles that follow the language

`title` is a plain class attribute — fixed when the class is defined, long before
a catalogue is loaded or the language changes. Name a catalogue key instead:

```python
class Settings(Screen):
    title = "Settings"                  # shown when the key is missing
    title_key = "settings.title"        # translated before every build()
```

The key is looked up again each time the screen's tree is built, and switching
the language rebuilds every screen on the stack, so titles (the action bar, the
mockup, `to_dict()`) always match the current language. `Screen("Fallback",
title_key="settings.title")` works too. A title that depends on data can be
assigned inside `build()`: `self.title = t("user.title", name=self.user)`.
A key the catalogue lacks leaves the fallback title and is logged once, like any
missing translation.

Find a widget anywhere in the current screen:

```python
self.find("counter").set_text("5")     # matches Label(..., id="counter")
```

Pass the class to get a typed result — editors and mypy then know it is a
`TextInput`, and a wrong guess fails right there instead of three calls later:

```python
name = self.find("name", TextInput)    # TextInput | None
name = self.get("name", TextInput)     # TextInput, or WidgetNotFoundError
labels = self.root.find_all(Label)     # list[Label]
```

`find()` returns `None` for a missing id; `get()` raises
`WidgetNotFoundError` (a `LookupError`) with a *did you mean* hint. Both raise
`WidgetTypeError` (a `TypeError`) when the widget is of another class.

### Screens as tabs: one shell owns the frame

**Only the screen the navigator shows is rendered.** `app.render()` — and every
automatic redraw behind it — serialises `navigator.current` and nothing else.
That single fact explains most of the surprises a tabbed app runs into:

| Call | On a screen the navigator owns | On a screen built *inside* another screen (a tab) |
| --- | --- | --- |
| `self.refresh()` | rebuilds the tree; the frame is drawn when that screen is current | rebuilds the tree; nothing displays it — logged once, with a pointer here |
| `self.on("event", …)` | works (the screen has a running app) | `PyMobileError` — see below |
| `self.app` | works | `PyMobileError` — a screen that was never pushed has no app |

Such a nested `Screen` is a *data holder*: `on_mount`/`on_show` never run for
it, `self.on()` cannot subscribe (a `PyMobileError` tells you so — subscribe
from `on_mount()` of a screen that actually gets pushed, or use a plain object),
and a `refresh()` on it repaints nothing. A screen stacked *below* the current
one is different: it keeps its rebuilt tree and shows it the next time it
becomes current, which is exactly what a language change relies on.

The pattern that works: a shell screen owns the frame, and the tabs are plain
objects that hold data and build widgets.

```python
class Shell(Screen):
    """The only screen that renders: BottomNavigation + the active tab."""

    def __init__(self) -> None:
        super().__init__()
        # Tabs are created out here, not in build(): refresh() runs build()
        # again, and a tab recreated there would forget the data it holds.
        self.tabs = [HomeTab(self), StatsTab(self)]
        self.active = self.tabs[0]

    def build(self) -> Widget:
        return Column(
            self.active.build(),                  # rebuilt by refresh(), below
            BottomNavigation(
                ["Home", "Stats"],
                value=self.active.title,
                on_select=self.show_tab,
            ),
        )

    def show_tab(self, name: str) -> None:
        self.active = next(tab for tab in self.tabs if tab.title == name)
        self.notify_data_changed()

    def notify_data_changed(self) -> None:
        """The single render point: every tab calls this, never self.refresh()."""
        self.refresh()


class HomeTab:
    """Data and widgets — not a Screen, so it cannot render by accident."""

    title = "Home"

    def __init__(self, shell: Shell) -> None:
        self.shell = shell
        self.items: list[str] = []

    def build(self) -> Widget:
        return Column(Label(f"{len(self.items)} items"), Button("Reload", on_press=self.reload))

    def reload(self) -> None:
        self.items = fetch_items()
        self.shell.notify_data_changed()          # the shell repaints
```

Two traps the pattern avoids: calling `self.refresh()` on a tab and waiting for
a frame that never comes, and *rebuilding during a frame* — a `refresh()` from
inside `build()` used to recurse until `RecursionError`. The framework now
defers a redraw requested while a frame is being built (and reports a tree that
redraws on every frame instead of spinning forever); the shell above is still
the right structure, because it keeps one place that decides what is on screen.

---

## Updating the screen

Change a widget and the screen redraws itself. There is no `render()` to
forget.

```python
class Home(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.taps = 0                          # state belongs to the screen,
                                               # not to the frame being built

    def build(self) -> Widget:
        self.counter = Label(f"Taps: {self.taps}")   # id becomes "counter"
        return Column(self.counter, Button("Tap", on_press=self.on_tap))

    def on_tap(self) -> None:
        self.taps += 1
        self.counter.text = f"Taps: {self.taps}"    # that is the whole update
```

`build()` runs again on every `refresh()` (and on a theme or language change),
so anything assigned there is reset. Keep the state in `__init__` or in a model
object and let `build()` read it — a theme switch must not zero the counter.

`self.counter.text = ...` and `self.counter.set_text(...)` do the same thing.
Every public attribute works this way — `text`, `value`, `checked`, `visible`,
`enabled`, and also `style`, `placeholder`, `Badge.color`, `Avatar.image`,
`ProgressText.label` and the rest: assigning a *different* value schedules a
redraw.

```python
label.style = Style(color=Color.ERROR, bold=True)   # redraws by itself
```

`Style` is frozen: `label.style.bold = True` raises `FrozenInstanceError`, and
even for a mutable object the in-place change would be invisible — the widget
compares the old value with the new one. Build a changed copy and assign it:

```python
from dataclasses import replace

label.style = replace(label.style, bold=True)       # redraws by itself
```

When the tree itself changed (rows added or removed), call `self.refresh()`.

Redraws are coalesced, so a handler that updates six widgets still produces a
single frame. Assigning a value that has not changed renders nothing at all,
and mutating a widget on a screen that is not visible is free.

Group updates explicitly when you build them in a loop:

```python
with app.batch():
    for label, value in zip(self.labels, values):
        label.text = value
# one render happens here
```

| Method | When to use |
| --- | --- |
| *(nothing)* | a property changed — handled for you |
| `self.refresh()` | the tree itself changed (rows added or removed) |
| `app.render()` | you want a frame pushed out right now |

`self.refresh()` repaints a screen the navigator owns; a screen embedded in
another screen's tree is rebuilt by the screen that builds it — see
[Screens as tabs](#screens-as-tabs-one-shell-owns-the-frame).

```python
def on_data_loaded(self) -> None:
    self.items = fetch_items()
    self.refresh()          # the number of rows changed
```

`App("Demo", auto_render=False)` restores the old manual behaviour; in that
mode the first missed redraw logs a warning rather than leaving you guessing.

---

## Threads and the UI

Widgets are not thread-safe; the UI state belongs to one place. Everything the
framework calls *for you* runs there:

| Callback | Runs on |
| --- | --- |
| button / input handlers, `on_mount`, `on_show`, … | the UI side |
| `app.set_interval` / `app.set_timeout` | the UI side (one frame per tick) |
| `app.run_job(...).then(...)` | the UI side |
| `app.http.get_async(...).then(...)` | the UI side |
| `app.dispatch(fn, *args)` | the UI side |
| the function passed to `run_job` / `repeat_job` | a worker thread |
| `set_interval(..., background=True)` | the timer thread |
| a standalone `HttpClient().get_async(...).then(...)` | the request thread |

"The UI side" is the event-loop thread on a device and the Tk thread in
`run --gui`; in tests and the browser preview it is whichever thread calls, but
under the lock that event handlers and rendering hold, so the calls never
interleave. So a job callback can simply update a widget:

```python
app.run_job(load_cards).then(lambda cards: self.show(cards))   # safe
```

From code that runs on your own thread — a `threading.Thread`, a `run_job`
body, a `background=True` timer, a plain `HttpClient` — hand the update over:

```python
def worker() -> None:
    data = slow_query()
    app.dispatch(self.show, data)    # show(data) runs on the UI side, one frame
```

`dispatch` returns `False` once the app has stopped. Keep UI-side callbacks
short: a timer that blocks for a second freezes the screen for a second —
put blocking work in `run_job` or a `background=True` timer.

`app.toast("Saved")` / `app.toast("Sync failed", long=True)` shows a short
platform message (on the desktop it is printed / shown in the preview). For a
message with an *Undo* button use [`app.snackbar()`](#snackbar).

---

## Themes

Light and dark themes with semantic colour palette.

```python
App("My App", theme="dark")          # or "light" (default)
App("My App", theme=Theme.dark())    # custom Theme object

app.set_theme("dark")                # switch at runtime — screen redraws
app.theme.is_dark                    # True / False
app.theme["PRIMARY"]                 # resolved colour
```

Built-in themes: `Theme.light()` and `Theme.dark()`. Custom themes use
`Theme(name, colors)` with your own palette.

The active theme is available everywhere via `app.theme`. `Color.PRIMARY` is a
fixed hex constant (`#3F51B5`); for the live theme use `app.theme["PRIMARY"]`
(or `app.theme.color("BACKGROUND")`).

---

## Storage

Key-value JSON storage with atomic writes.

```python
app.storage["token"] = "abc123"      # set
token = app.storage["token"]         # get
del app.storage["token"]             # delete (KeyError with hint if missing)

app.storage.get("token", default="") # with default
app.storage.contains("token")        # explicit check
app.storage.exists("token")          # alias, more discoverable
"token" in app.storage               # also works
app.storage.keys()                   # all keys
app.storage.clear()                  # wipe everything
```

Data persists across app restarts. On Android it uses the app's private files
directory; on desktop `~/.pymobile/<package>.json` with the dots replaced by
dashes (`com.example.notes` → `com-example-notes.json`), so two projects open
side by side never overwrite each other's data. A file named with the dots
(`com.example.notes.json`, e.g. restored by hand) is adopted automatically the
first time the dashed one is missing. Override the directory for every app
with `PYMOBILE_STORAGE_DIR`.

```python
App("My App", storage_path="/custom/dir")         # a directory
App("My App", storage_path="/custom/store.json")  # or an explicit file
```

### Atomic sequences

Every single operation is atomic. A read-modify-write **sequence** you write
by hand is not — two jobs incrementing the same counter can lose an update.
Use these instead:

```python
app.storage.increment("taps")                       # atomic +1
app.storage.update("cart", lambda items: [*items, new], default=[])
app.storage.setdefault("device_id", make_id())

with app.storage.transaction() as store:            # multi-step
    balance = store.get("balance", 0)
    store["balance"] = balance - price
    store["history"] = [*store.get("history", []), price]
```

---

## Background jobs

Run work off the UI thread without blocking the screen.

```python
handle = app.run_job(lambda: fetch_heavy_data())
handle.then(on_success=lambda data: self.show(data),
            on_error=lambda err: self.show_error(err))
handle.wait(timeout=10)   # block until done (rare)
handle.cancel()

# Repeating job
handle = app.repeat_job(5000, lambda: poll_server())
handle.cancel()            # stop when done
```

`JobHandle` exposes `.done`, `.cancelled`, `.result`, `.error`.

`then()` callbacks run on the UI side (see
[Threads and the UI](#threads-and-the-ui)). If `on_success` raises, the
error is logged with its traceback and passed to `on_error`; the job's own
`result` is kept. `cancel()` suppresses the callbacks; Python cannot interrupt a
function that is already running, so `done` turns true only when it returns.

---

## HTTP cache and offline mode

Disk-backed cache for GET requests.

```python
from pymobile import HttpCache, HttpClient

client = HttpClient(
    base_url="https://api.example.com",
    cache=HttpCache(),          # or HttpCache("http-cache.json")
)

# Cached request — returns instantly from cache if fresh
response = client.get_cached("/items", ttl=300)   # 5 minutes

# If network fails, stale cache is returned (offline mode)
# response.from_cache tells you if the data came from cache
```

---

## Async HTTP

Non-blocking requests that don't freeze the UI.

```python
future = client.get_async("/items")
future.then(
    on_success=lambda r: self.update_list(r.json()),
    on_error=lambda err: self.show_error(err),
)

future.done       # True when finished
future.cancel()   # suppress callbacks
result = future.get(timeout=10)   # block if needed
```

Methods: `get_async`, `post_async`, `put_async`, `delete_async`.

---

## Input validation

Pure logic, no UI dependency.

```python
from pymobile import Validator

v = Validator({
    "email":    ["required", "email"],
    "name":     ["required", {"min_length": 2}, {"max_length": 50}],
    "age":      ["optional", "integer", {"between": [1, 120]}],
})

errors = v.validate({"email": "bad", "name": ""})
# {"email": "must be a valid email address", "name": "is required"}

v.validate_or_raise({"email": "bad"})   # raises ValidationError
```

Empty values — `None`, `""`, whitespace only, an empty list — follow three
rules:

* `required` reports `"is required"` (whitespace does not count as a value);
* `optional` lets the field stay empty; its other rules run only when it is
  filled — except `matches`, which still fails while the other field is
  filled, so a password with an empty confirmation is rejected;
* a field with neither is validated as is: `["email"]` rejects `""`.

`integer` accepts plain digits with an optional sign (not `"4_2"`), and
`number` rejects `"nan"` and `"inf"`.

Validators come in two forms:

* **bare strings** — `required`, `optional`, `email`, `integer`, `number`,
  `boolean`. Use them as-is: `["required", "email"]`.
* **rules with an argument** — `length`, `min_length`, `max_length`,
  `between`, `min`, `max`, `matches`, `one_of`, `regex`. These always need a
  value, so write them as a one-key mapping: `{"min_length": 2}`,
  `{"between": [1, 120]}`. A bare `"min_length"` is an error — there is no
  sensible default length.

### Translating the messages

The built-in messages are English, but they are not hard-wired. Every rule has
a stable **id** and an English default (`DEFAULT_MESSAGES` in
`pymobile.core.validation`); a message is taken from the first of these that
has it:

1. `Validator(..., messages={...})` — for one validator. The key is a rule id
   (`"required"`) or `"field.rule"` for a single field (`"email.required"`); the
   value is a `str.format` template, or a callable that receives the rule's
   parameters as keyword arguments (handy for plural forms).
2. the translation catalogue, under `validation.<rule id>` — **one** translation
   for every validator in the app, looked up when the message is produced, so a
   language switch at run time is followed.
3. the English default.

```python
Validator(
    {"email": ["required", "email"], "name": ["required", {"min_length": 2}]},
    messages={
        "required": "обов'язкове поле",
        "email.email": "невірна адреса пошти",          # only the email field
        "min_length": "не менше {minimum} символів",
    },
)

# or once, for the whole app — the same ids, prefixed with "validation.":
translations.load({
    "validation.required": "обов'язкове поле",
    "validation.email": "невірна адреса пошти",
    "validation.min_length": "не менше {minimum} символів",
}, language="uk")
```

| Rule id | Default text | Placeholders |
| --- | --- | --- |
| `required` | `is required` | |
| `email` | `must be a valid email address` | |
| `min_length` / `max_length` | `must be at least {minimum} characters` / `must be at most {maximum} characters` | `minimum`, `maximum` |
| `integer` / `number` / `boolean` | `must be an integer` / `a number` / `a boolean` | |
| `between` / `min` / `max` | `must be between {low} and {high}` | `low`, `high` |
| `matches` | `does not match` (the `{"matches": "field"}` form: `does not match {field!r}`) | `field` |
| `one_of` | `must be one of: {choices}` | `choices` |
| `regex` | `must match {pattern!r}` | `pattern` |

`matches_field` (the field form of `matches`) and `length` (both ends of a
length rule) are also ids: an override for `matches` or `length` covers them
unless the more specific id is overridden too. An id that does not exist —
`messages={"requird": …}` — is an error at construction, not a message that
silently never shows. The bare rule functions (`required("")`) return the same
translated text, and a rule you write yourself keeps its own message.

---

## Plugins

Extend the app without touching its code.

```python
from pymobile import Plugin, plugins

class AnalyticsPlugin(Plugin):
    name = "analytics"

    def activate(self, app):
        app.on("screen:change", self.track)

    def track(self, event):
        print(f"screen: {event.source}")

plugins.register(AnalyticsPlugin())
# Activated automatically in App.run()
```

Hooks: `activate(app)`, `on_app_start(app)`, `on_app_stop(app)`.

---

## Reacting to events

Subscribe from a screen and the handler is removed when the screen goes away:

```python
class TimerScreen(Screen):
    def on_mount(self) -> None:
        self.on("pomodoro:tick", self.on_tick)   # cancelled on unmount

    def on_tick(self, event) -> None:
        self.clock.text = event.get("remaining")
```

With `app.on()` the handler of a popped screen stays subscribed: it keeps
firing, keeps the screen alive, and runs twice as soon as the screen is pushed
again. `self.on()` avoids that. To bind a handler registered elsewhere, pass
the screen: `app.on("tick", handler, screen=self)`.

---

## Notifications

```python
app.notify("Done", "File saved")               # returns a notification id
app.notify("Syncing", "In progress…", ongoing=True)   # cannot be swiped away
app.notifications.cancel(notification_id)
```

The notification channel is created automatically. On Android 13+ you need the
`POST_NOTIFICATIONS` permission — see below.

### Requirements for notifications to work

1. **Declare the permission in `pymobile.toml`:**

```toml
permissions = [
    "android.permission.INTERNET",
    "android.permission.POST_NOTIFICATIONS",
]
```

2. **Request the permission at runtime** (after the screen is visible):

```python
from pymobile import Permission

results = self.app.permissions.request(Permission.POST_NOTIFICATIONS)
if results.get(Permission.POST_NOTIFICATIONS.value, False):
    self.app.notify("Title", "Body")
```

3. **Check device settings** — ensure notifications are enabled for the app:
   *Settings → Apps → Your App → Notifications*.

`pymobile build` reminds you of step 1 only when it is needed: if the project
targets Android 13+ (the default), does not declare `POST_NOTIFICATIONS`, **and**
its code posts a notification (`app.notify(...)`, `app.notifications.notify(...)`,
`Notifications(...)`), the build prints
`targetSdk >= 33 without POST_NOTIFICATIONS: notifications stay hidden` with the
file and line of the call. An app that never notifies gets no warning. The scan
covers the files that ship in the APK (`exclude`, `tests/` and virtualenvs are
skipped) and ignores comments and docstrings.

A default `pymobile build --native` is enough. `DeviceServices.notify()`
creates the notification channel on every post, so you do not need to rebuild
the JNI bridge with `PYMOBILE_BUILD_JNI=1`. That flag is only for changing
the C source (see [Contributing](#contributing)).

### Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Permission dialog doesn't show | Missing from `pymobile.toml` | Add `POST_NOTIFICATIONS` to `permissions` |
| `notify()` returns ID but nothing shows | System notifications disabled | Enable in device Settings → Apps → Notifications |
| `SecurityException` in logcat | Permission not granted | Request permission at runtime |

---

## Vibration

```python
app.vibrate(100)                               # one shot, milliseconds
app.vibration.preset("success")
app.vibration.pattern([0, 100, 50, 100])       # [wait, buzz, wait, buzz]
app.vibration.cancel()
```

Presets: `tick`, `click`, `double`, `success`, `error`, `heartbeat`.

Pulses shorter than about 50 ms cannot be felt on most hardware, so short
values are stretched automatically and waveforms are sent with explicit
amplitudes — several devices silently ignore the default.

---

## Permissions

```python
from pymobile import Permission

app.permissions.has(Permission.CAMERA)              # bool
app.permissions.request(Permission.CAMERA)          # {'android.permission.CAMERA': True}
app.permissions.missing([Permission.CAMERA, "VIBRATE"])
app.require_permissions(Permission.CAMERA)          # raises PermissionError_ if denied
```

Three spellings are accepted: `"CAMERA"`, `"android.permission.CAMERA"` and
`Permission.CAMERA`.

### Three rules that save hours

**1. Declare every permission in `pymobile.toml`.** Android denies an
undeclared permission *silently* — no dialog appears at all. The compiler warns
you when code requests something the manifest lacks:

```
! requested in code but not declared in pymobile.toml: android.permission.CAMERA
  — Android will deny them without showing a dialog
```

**2. Ask after the first screen is visible.** A request issued before the
window exists is dropped by the system. Put it in `on_show()`, not before
`app.run()`:

```python
def on_show(self) -> None:
    if self.visits == 1:
        self.app.permissions.request(Permission.CAMERA)
```

**3. Two refusals disable the dialog forever.** Android then returns an instant
denial. PyMobile detects this and opens the app's settings page with an
explanation, instead of failing mutely.

---

## HTTP requests

A client built on the standard library — no third-party dependency, so nothing
extra lands in the APK.

```python
from pymobile import HttpClient

client = HttpClient(
    base_url="https://api.example.com",
    headers={"Authorization": "Bearer ..."},
    timeout=10,
    retries=2,           # exponential backoff
)

client.get("/items", params={"page": 2})
client.post("/items", json={"name": "Widget"})
client.put("/items/1", json={"name": "Updated"})
client.delete("/items/1")
```

The response object:

```python
response = client.get("/items")

response.status      # 200
response.ok          # True for 2xx
response.json()      # parsed JSON
response.text        # decoded text
response.content     # raw bytes
response.headers     # lowercase keys
response.elapsed     # seconds
```

**Error statuses are returned, not raised** — they are valid HTTP responses:

```python
response = client.get("/missing")
if not response.ok:
    print(response.status)        # 404

response.raise_for_status()       # raises NetworkError for 4xx/5xx
```

Retries cover connection failures and 408/425/429/5xx.

HTTPS works out of the box: a bundle of root certificates is packaged into the
APK, because Android provides no certificate file where OpenSSL looks for one.

For production, restrict where the client may go:

```python
from pymobile import HttpClient, HttpSecurityPolicy

client = HttpClient(
    base_url="https://api.example.com",
    security=HttpSecurityPolicy(
        require_https=True,                        # plain http:// is refused
        allowed_hosts={"api.example.com", "cdn.example.com"},
    ),
)
```

A blocked URL raises `NetworkError` before anything is sent. The policy is
checked again on every redirect, so a server cannot bounce a request to
another host or downgrade it to HTTP, and `Authorization`/`Cookie` headers are
dropped when a redirect leaves the original origin. Host names are compared
case-insensitively. The default policy allows everything, for local
development.

Remember to list `android.permission.INTERNET` in your config.

---

## Languages

The device language plus a small catalogue, which is usually all a mobile app
needs. `gettext` and `.mo` files keep working — the standard library is
packaged in full.

```python
from pymobile import t, translations, device_language

translations.load_dir("locales")          # locales/en.json, locales/uk.json …
translations.use(device_language(default="en"))

Label(t("greeting", name="Оксана"))       # "Привіт, Оксана!"
```

```json
{
  "greeting": "Привіт, {name}!",
  "items": {
    "one":  "{count} елемент",
    "few":  "{count} елементи",
    "many": "{count} елементів"
  }
}
```

```python
t("items", count=1)     # 1 елемент
t("items", count=3)     # 3 елементи
t("items", count=5)     # 5 елементів
```

The plural form is chosen by the CLDR rule of the catalogue's *language* —
Ukrainian and Russian (`one`/`few`/`many`), Polish (21 → `many`: *21 plików*),
Czech, Slovak, the Baltic and South Slavic languages, Romanian, Slovenian,
Arabic, Hebrew, Irish, Welsh, French/Portuguese (0 and 1 → `one`), languages
without plurals (Chinese, Japanese, Korean, …), and the English rule for the
rest. A form missing from the catalogue falls back to `other`, then `many`.
An explicit `zero` form is used for 0 in any language.
`plural_category(21, "pl")` returns the category directly. Lookup falls back from `pt-br` to `pt` and
then to the default language, and a missing key renders as the key itself
(logged once) rather than raising in the middle of a screen.

### Keys are flat — a dot is part of the key

A catalogue is **one level** of `"key": "text"` pairs. `t("stats.balance")`
looks for a key literally named `stats.balance`; it does not descend into
`{"stats": {"balance": …}}`. Nothing about a miss like that is dramatic — the
screen shows the key itself — so a catalogue written as sections looks like a
translation bug rather than a structure problem. The framework now says so:
loading such a file logs a warning naming the nested objects, and each missed
dotted key is logged with a hint.

```
{ "stats": { "balance": "Баланс" } }    ✗  t("stats.balance") → "stats.balance"
{ "stats.balance": "Баланс" }           ✓  the whole key, dot included
```

Prefixes keep flat files readable — `"auth.title"`, `"auth.error"`,
`"stats.balance"`, … all at the top level. If you would rather keep sections in
the source file, expand them at load time:

```python
translations.load_dir("locales", flatten=True)      # {"stats": {"balance": …}} → "stats.balance"
```

```python
from pymobile import flatten_catalogue              # or expand one mapping yourself

translations.load(flatten_catalogue({"stats": {"balance": "Баланс"}}), language="uk")
```

The one nested shape understood without `flatten=True` is a plural form map,
whose keys are all CLDR quantity names (`zero`/`one`/`two`/`few`/`many`/`other`):

```json
{ "items": { "one": "{count} елемент", "few": "{count} елементи", "many": "{count} елементів" } }
```

Any other nested object is a namespace, so `flatten_catalogue()` turns it into
dotted keys — name a section after its content, not after a quantity.

Switching language redraws whatever is on screen — `translations.use("uk")` is
enough, because `t()` runs inside `build()` and the screen is rebuilt for you.

`device_language()` reads the real system setting on Android — including
Android 13 per-app language overrides — and the usual environment variables on
a desktop. Force one during development with `PYMOBILE_LANGUAGE=uk`.

Already using xgettext? `translations.install_gettext("app", "locale")` reads
your compiled `.mo` catalogues instead.

### Numbers, dates and money

Formatting follows the language too — no Babel, no ICU, nothing to install:

```python
from pymobile import format_number, format_percent, format_currency, format_date

format_number(1234567.891, 2)            # uk: 1 234 567,89   en: 1,234,567.89
format_percent(0.256)                    # uk: 26%            de: 26 %
format_currency(1250, "UAH")             # uk: 1 250,00 ₴     en: ₴1,250.00
format_currency(9.5, "USD", language="en")   # $9.50
format_date(date(2026, 10, 1), "long")   # uk: 1 жовтня 2026 р.  en: October 1, 2026
format_date(day, pattern="d MMMM")       # uk: 1 жовтня (the genitive month)
format_time(time(14, 5))                 # uk: 14:05          en: 2:05 PM
```

The language is the active catalogue's (`translations.use(...)`) unless you
pass `language=`. Built in: English (US, GB, AU, CA, IE), Ukrainian, Russian,
Polish, Czech, German (DE, AT, CH), French (FR, CA, CH), Spanish (ES, MX, US),
Italian, Portuguese (BR, PT), Dutch, Romanian and Turkish; other languages use
English formats. Numbers are rounded half-to-even from their decimal value,
so `0.1 + 0.2` prints as `0.3`.

Inside a catalogue the same formats are placeholder specifiers, so the
translator decides the wording and the code passes raw values:

```json
{ "total": "Разом: {sum:currency:UAH}, до {day:date:long}" }
```

```python
t("total", sum=1250, day=date(2026, 10, 1))   # Разом: 1 250,00 ₴, до 1 жовтня 2026 р.
```

Specifiers: `number`, `number:2`, `percent`, `percent:1`, `currency:EUR`,
`date`, `date:long` (`short`/`medium`/`long`/`full`), `time`, `time:medium`,
`datetime`. Anything else is Python's own format spec (`{x:.2f}`). A value
that does not fit its specifier is logged and the text is shown unformatted
rather than crashing the screen.

---

## Events

A synchronous event bus decouples UI from application logic.

```python
# framework events
app.on("app:start",     lambda e: ...)
app.on("app:stop",      lambda e: ...)
app.on("app:render",    lambda e: ...)
app.on("app:locale",    lambda e: ...)   # system language changed; e.source = "uk-UA"
app.on("screen:change", lambda e: print(e.source))

# your own events
app.events.emit("cart:updated", source="CartScreen", count=3)
app.on("cart:updated", lambda e: print(e.get("count")))

subscription = app.on("x", handler)
subscription.cancel()          # removes THIS subscription only

# remove handlers
app.off("cart:updated")                 # removes all, returns count
app.off("cart:updated", handler)        # every registration of handler
app.off("cart:updated", self.on_cart)   # bound methods work too
app.events.off_all("cart:updated")      # same as off(name), via EventBus
```

`app.on("event", None)` raises `TypeError` immediately instead of failing
silently later. Handlers are compared with `==`, so `off()` recognises a bound
method even though `self.on_cart` is a new object each time. If the same
function is subscribed twice (two screens), each `Subscription.cancel()` removes
only its own registration. Inside a screen, `self.on(...)` is cancelled for you
when the screen unmounts.

An exception inside one handler is logged and never prevents the others from
running.

---

## Timers

Schedule work without importing `threading`. Callbacks run on the UI side,
like a button handler, so a tick can update widgets directly and draws one
frame; the same code runs on desktop and device. For blocking work — a network
poll — pass `background=True` and hand results over with `app.dispatch()`
(see [Threads and the UI](#threads-and-the-ui)).

```python
# every second — drive a clock, poll a sensor, tick a game
ticker = app.set_interval(1000, self.on_tick)

# once, after a delay — dismiss a splash, auto-save
app.set_timeout(250, splash.dismiss)

# stop early
ticker.cancel()
```

Every call returns a `TimerHandle` with `.cancel()` and `.cancelled`. An
exception inside a callback is logged and does **not** stop a repeating
timer. All timers are cancelled automatically when the app stops, but you
should still cancel a repeating timer in `on_unmount()` so a popped screen
does not keep ticking:

```python
def on_unmount(self) -> None:
    self.ticker.cancel()
```

### Intervals do not drift

Ticks are scheduled against a fixed timeline — tick *n* is due at
`start + n × interval` — so the time your callback spends working is not added
to the next wait. A one-second timer is still on the second an hour later,
which matters for clocks, metronomes and games.

```python
app.set_interval(1000, self.tick)                          # aligned (default)
app.set_interval(1000, self.poll, drift_correction=False)  # fixed pause between runs
```

Deadlines missed while the device was asleep are skipped rather than fired as
a burst, so a metronome stays in phase instead of stuttering to catch up. Use
`drift_correction=False` for a poller that must not overlap itself.

---

## Configuration reference

Everything lives in `pymobile.toml` (or a `[tool.pymobile]` table inside
`pyproject.toml`).

```toml
[app]
# --- identity ---
name = "My App"                 # shown under the launcher icon
package = "com.example.myapp"   # reverse-DNS, lowercase segments
version = "1.0.0"
version_code = 1                # integer; must increase for each Play upload

# --- entry point ---
entrypoint = "main.py"
source_dir = "."

# --- Android platform ---
min_sdk = 24                    # Android 7.0 — the lowest CPython 3.14 runs on
target_sdk = 35                 # Android 15
orientation = "portrait"        # portrait | landscape | sensor | user

# Every permission requested at runtime must be listed here.
permissions = [
    "android.permission.INTERNET",
    "android.permission.VIBRATE",
    "android.permission.POST_NOTIFICATIONS",
]

# --- resources ---
icon = "assets/icon.png"        # optional; default icon used otherwise
allow_backup = false            # keep the private store out of cloud/adb backups

# --- build ---
abis = ["arm64-v8a"]
output_dir = "build"
optimize = false                # ship bytecode instead of sources (needs a Python 3.14 build host)
strip_debug = true              # -OO: drop docstrings and asserts
exclude = ["docs/**", "secrets/**"]   # added to the built-in defaults
exclude_only = false                    # true = use exclude as written
```

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `PyMobile App` | display name |
| `package` | `org.pymobile.app` | unique application id |
| `version` | `0.1.0` | user-visible version |
| `version_code` | `1` | internal build number |
| `entrypoint` | `main.py` | module to execute |
| `source_dir` | `.` | root of your sources |
| `min_sdk` | `24` | oldest supported Android; lower values are raised to 24 with a warning |
| `target_sdk` | `35` | Android version you target |
| `orientation` | `portrait` | screen orientation |
| `permissions` | `["…INTERNET"]` | manifest permissions |
| `icon` | *(none)* | path to a square PNG |
| `allow_backup` | `false` | let Android back up the app's private data |
| `abis` | `["arm64-v8a"]` | architectures |
| `output_dir` | `build` | where the APK is written |
| `optimize` | `false` | package `.pyc` when enabled; for `--native` only when the build runs on Python 3.14 (the device's version), otherwise sources are shipped with a warning — which is why the `init` template ships `optimize = false` too |
| `strip_debug` | `true` | compile with `-OO` |
| `exclude` | *(none)* | glob patterns to skip, **added to** the built-in list (`**/__pycache__/**`, `**/*.pyc`, `**/tests/**`, `tests/**`, `**/test_*.py`, `.git/**`, `.venv/**`, `venv/**`, `build/**`, `dist/**`) |
| `exclude_only` | `false` | `true` = `exclude` replaces the built-in list (advanced; the build then warns if `output_dir` would ship itself) |

Every value is validated **before** the build starts, and each error carries a
fix:

```
✗ Invalid package name 'Bad_Package'
  hint: Use reverse-DNS with lowercase segments, e.g. com.example.myapp
```

`exclude` **adds** to the built-in patterns: writing `exclude = ["docs/**"]`
never drops the `build/**` rule, so the output directory (and the previous APK
inside it) is not packaged. `exclude_only = true` turns that off and the build
then warns when it can see that `output_dir` would ship itself. The defaults are
exported as `pymobile.core.config.DEFAULT_EXCLUDE`.

Inspect the resolved configuration with `pymobile info` or
`pymobile info --json`.

---

## Building an APK

```bash
pymobile build --native                    # incremental
pymobile build --native --clean            # ignore the cache
pymobile build --native -v                 # per-stage timings
pymobile build --native --icon logo.png    # override the icon
pymobile build --native --output dist      # different directory
pymobile build --native --no-optimize      # ship .py for debugging
pymobile build --native --minimal-stdlib   # drop desktop-only stdlib
pymobile build --native --no-ssl           # drop OpenSSL (no HTTPS)
pymobile build --native --abi x86_64       # for the emulator
```

> Without `--native` you get a lightweight structural package used for quick
> checks. **It does not install on a device.**

A rebuild with no changes finishes instantly:

```
✓ up to date: my-app-1.0.0.apk (16.6 MB)
```

The cache tracks every input: sources, configuration and icon.

**Inside the APK:**

```
AndroidManifest.xml          generated from your config
classes.dex                  launcher activity and the view renderer
resources.arsc               compiled resources
lib/arm64-v8a/*.so           CPython, OpenSSL, SQLite, the JNI bridge
assets/app/                  your code and the pymobile package
assets/python/               standard library and CA certificates
res/mipmap-*/icon.png        icons, five densities
META-INF/                    signature (v2+v3)
```

Builds are reproducible: identical inputs produce an identical APK payload.
Every zip entry carries a fixed timestamp, so the archive does not change with
the clock; the signature block itself (`META-INF/*.RSA`) is produced by
`apksigner` and is the one part that may differ between two runs. The file is
written to a temporary path and moved into place, so an interrupted build never
leaves a corrupt artifact.

### Running on the emulator (x86_64)

The Android Studio emulator runs x86_64 system images, and an arm64 APK
does not start there. Build an x86_64 one — the prebuilt bridge ships for
both architectures, so still no NDK:

```bash
pymobile build --native --abi x86_64       # → build/my-app-1.0.0-x86_64.apk
adb install -r build/my-app-1.0.0-x86_64.apk
```

`--abi` overrides `abis` from `pymobile.toml` for one build. The emulator APK
gets an `-x86_64` suffix so it never overwrites the phone build. Create the
virtual device in Android Studio (*Device Manager → Create device*, an
**x86_64** image with API 24 or newer), start it, then `adb install` as with a
phone; `adb logcat -s pymobile python` shows your app's output.

### Signing

Android installs an update only if it is signed with the same key as the
installed app.

**Debug builds** are signed with a per-app key created on the first build in
`~/.pymobile/keystores/<package>-debug.jks` — outside `build/`, so
`pymobile clean` and `build --clean` keep the signature. Set
`PYMOBILE_KEYSTORE_DIR` to share the key between machines or CI runs (store it
as a CI secret). A key left in `build/` by pymobile ≤ 0.7.3 is adopted
automatically.

**Release builds** use your own keystore. Pass the passwords through the
environment, not the command line (arguments end up in shell history and
`ps`):

```bash
export PYMOBILE_KS_PASS='…'          # keystore password
export PYMOBILE_KEY_PASS='…'         # key password (defaults to the keystore's)
pymobile build --native --keystore release.jks --key-alias upload
```

`--ks-pass` / `--key-pass` still work but print a warning. Keep the keystore
and its passwords backed up: without them you cannot publish updates.

### Size

A hello-world APK is **16.6 MB**, down from 21.6 MB in 0.2.0. The official
CPython Android runtime ships every support library twice — `libcrypto.so`
and `libcrypto_python.so` are byte-identical, and only the `_python` names are
actually linked — so 5 MB of each APK was the same three libraries repeated.
That is fixed for every build; no flag needed.

Two opt-in flags trim further:

| Flag | Saves | Cost |
| --- | --- | --- |
| `--minimal-stdlib` | ~1.7 MB | no `pydoc`, `unittest`, `venv`, `pdb`, `xmlrpc` on device |
| `--no-ssl` | ~4.7 MB | no HTTPS at all — `HttpClient` over TLS stops working |

With both, the same app is **11.7 MB**. `--no-ssl` only makes sense for an app
that talks to nothing, or over plain HTTP on a local network.

```
21.6 MB   0.2.0
16.6 MB   0.3.0                              duplicate libraries removed
11.7 MB   0.3.0 --minimal-stdlib --no-ssl
```

---

## Application icon

```toml
icon = "assets/icon.png"
```

Provide a square PNG, JPG or WebP — 512×512 works best. It is resized into all
five densities (48, 72, 96, 144 and 192 px). Install the `icons` extra for
high-quality resampling:

```bash
pip install "pymobile-framework[icons]"
```

Without it the icon is copied unscaled; the build still succeeds. With no icon
configured, a bundled default is used, so a build never fails over a missing
asset:

<p align="center">
  <img src="docs/icon.png" alt="The bundled PyMobile launcher icon" width="128">
</p>

(The PyMobile mark is also the icon of anything built with
`pymobile init` before you replace it — `icon = "assets/icon.png"` is all it
takes.)

---

## Installing on a phone

**Without a cable:** copy the `.apk` to your device through cloud storage or a
messenger, open it there and allow installation from unknown sources.

**Over USB**, with USB debugging enabled:

```bash
adb install -r build/my-app-1.0.0.apk
```

`adb` lives in `~/.andro/sdk/platform-tools/`.

---

## Debugging

Everything your app prints, including tracebacks, goes to logcat:

```bash
adb logcat -s pymobile pymobile.stdout pymobile.stderr
```

| Tag | Contents |
| --- | --- |
| `pymobile` | framework and renderer messages |
| `pymobile.stdout` | your `print()` output |
| `pymobile.stderr` | exceptions and tracebacks |

If a single widget fails to render, it is replaced by red text naming the
error while the rest of the screen keeps working — on the device and in every
desktop preview alike. The same applies to a callback that raises: the failure
is logged with its traceback and the app stays alive, so one bad handler cannot
close the window.

### Diagnostics

```python
from pymobile import get_diagnostics

info = get_diagnostics()
# {"framework_version": "0.9.2", "platform": "android",
#  "python": "3.14.0", "log_level": "debug", "handlers": [...]}
```

### File logging

```python
App("My App", log_level="debug", log_file="app.log")
```

---

## Testing your app

`pip install pymobile-framework` ships a pytest plugin, so a test of your app
needs no scaffolding: an app on a recording stub bridge, storage in a temporary
folder, a driver that walks the screens like a user, and screenshot-style
snapshots of a screen's text picture. The fixtures are there as soon as
pymobile is installed (with pytest 7 or newer) — no `conftest.py`, nothing to
import:

| Fixture | What you get |
| --- | --- |
| `pymobile_session` | app + stub bridge + driver, set up and torn down for you: `.start(screen)`, `.app`, `.bridge`, `.driver`, `.screen` |
| `pymobile_app` | the `App` (created, not running — `app.run(FirstScreen())`) |
| `pymobile_bridge` | the recording `StubBridge`: `calls_named(...)`, `notifications`, `last_tree` |
| `pymobile_driver` | a `Driver`: `open`, `press`, `change`, `back`, `navigate`, `find` |
| `pymobile_snapshot` | compares the current screen's text picture with a golden file |

```python
def test_the_quiz_flow(pymobile_session):
    session = pymobile_session
    session.start(Menu())
    session.driver.navigate("Menu.start, Quiz.answer=42, Quiz.next")   # press, type, press
    assert session.driver.find("score").text == "42/10"


def test_the_menu_looks_right(pymobile_session, pymobile_snapshot):
    pymobile_session.start(Menu())
    pymobile_snapshot()            # snapshots/test_menu__test_the_menu_looks_right.txt


def test_open_a_screen_directly(pymobile_session):
    pymobile_session.start(Menu())
    pymobile_session.driver.open("ResultScreen", score=7)     # by name, with arguments
    assert pymobile_session.screen.score == 7
```

**Navigation in tests.** `driver.press(id)`, `driver.change(id, value)` (types
into a `TextInput`, picks a `Dropdown` option, sets a `Slider`, flips a `Switch`
with `True`/`False`), `driver.back()` and `driver.navigate("Menu.start, Quiz.next")`
all go through the same path as a real tap, so callbacks, navigation and
redraws run exactly as on the phone. A step is `[Screen.]widget[=value]`; the
`Screen.` prefix asserts where the step is taken, so a route that drifted fails
at the step that matters. A wrong widget id names the closest ones
(`Did you mean 'start'?`), and pressing a disabled widget or a `Label` is an
error, as it would be for a finger. If your code builds its own `App` (a
`main()`), call it and then `Driver.attach()`.

**Snapshots.** `pymobile_snapshot()` renders the screen with `render_ascii` and
compares it with `snapshots/<test module>__<test name>.txt` next to the test
(`pymobile_snapshot(widget, "name")` picks the file; a second unnamed check in
one test gets `_2`). A missing file is written and the test passes with a
warning — unless the `CI` environment variable is set, where a snapshot nobody
committed is a failure. A changed screen fails with a diff; accept it with
`pytest --pymobile-update-snapshots` (or `PYMOBILE_UPDATE_SNAPSHOTS=1`) and
commit the file.

**Isolation.** After every test the app is stopped, the process-wide bridge is
reset and the language the test switched to is undone; the store lives in
`tmp_path`. The plugin is active automatically; turn it off with
`pytest -p no:pymobile.testing.plugin`. Everything behind the fixtures is
importable without pytest (`from pymobile.testing import app_session, Driver`)
for other runners.

### Without the fixtures

`StubBridge` records every platform call, so no emulator is needed and tests
run in milliseconds.

```python
from pymobile import App, Button, Column, Label, Screen, Widget
from pymobile.core.bridge import StubBridge


class ProfileScreen(Screen):
    def build(self) -> Widget:
        # Save explicit references to widgets for direct test access
        self.status_label = Label("Offline")
        self.login_btn = Button("Log In", on_press=self.on_login)
        return Column(self.status_label, self.login_btn)

    def on_login(self) -> None:
        self.status_label.text = "Online"
        self.app.notify("Welcome", "You have logged in successfully")


def test_login_updates_ui_and_notifies():
    bridge = StubBridge(verbose=False)
    app = App("Test", bridge=bridge)
    screen = ProfileScreen()
    app.run(screen)

    # Trigger action directly on the widget reference
    screen.login_btn.press()

    # Verify UI state change
    assert screen.status_label.text == "Online"

    # Verify platform notification was sent via StubBridge
    assert bridge.calls_named("notify")
    spec = list(bridge.notifications.values())[0]
    assert spec.title == "Welcome"


def test_permission_denial_is_handled():
    denying = StubBridge(grant_permissions=False, verbose=False)
    app = App("Test", bridge=denying)
    assert app.permissions.request("CAMERA") == {"android.permission.CAMERA": False}
```

Useful members: `calls`, `calls_named(name)`, `notifications`, `granted`,
`reset()`, and the `grant_permissions=False` constructor flag.

Every platform call is recorded in `bridge.calls` under one of these names,
so assertions never guess:

| Recorded name | Written by |
| --- | --- |
| `notify` | `app.notify(...)` |
| `cancel_notification` | `app.notifications.cancel(...)` |
| `vibrate` | `app.vibrate(...)` |
| `vibrate_pattern` | `app.vibration.pattern(...)` — and every `preset(...)`, presets being patterns |
| `cancel_vibration` | `app.vibration.cancel()` |
| `toast` | `bridge.toast(...)` |
| `request_permissions` | `app.permissions.request(...)` |
| `render` | every frame pushed to the platform |

Note the fourth row: a haptic assertion is
`bridge.calls_named("vibrate_pattern")`, **not** `"vibrate"` — `preset("success")`
plays a pattern.

---

## Error handling

Every exception derives from `PyMobileError` and carries a `message` and an
actionable `hint`.

```python
from pymobile.errors import PyMobileError, NetworkError, ConfigError

try:
    response = client.get("/items")
except NetworkError as error:
    print(error.message)   # what happened
    print(error.hint)      # how to fix it
except PyMobileError:
    ...                    # catch anything from the framework
```

| Exception | Raised when |
| --- | --- |
| `ConfigError` | configuration missing or invalid |
| `NetworkError` | request failed, host unreachable, invalid JSON |
| `PermissionError_` | the user declined a permission |
| `ResourceError` | an icon or template could not be read |
| `BridgeError` | a call into Android failed |
| `PlatformError` | feature unavailable on this platform |

The CLI prints only the message and hint. Add `-v` for a full traceback.

---

## Desktop preview

`pymobile preview` renders a screen into a picture right on your laptop — no
emulator, no phone. It runs your entry point with the stub bridge and draws the
resulting widget tree:

```bash
pymobile preview            # text picture in the terminal
pymobile preview --ids      # annotate each widget with its id
pymobile preview --png ui.png   # a mockup of the screen as the phone draws it (needs Pillow)
pymobile preview --png ui.png --size 360x640 --theme dark
pymobile preview --png ui.png --text    # the text picture as an image (the pre-0.8 output)
```

`--theme dark` switches the application itself (`App.set_theme("dark")`) before
the tree is built, so a `build()` that reads `self.app.theme["SURFACE"]` lays
out and paints in that palette — the mockup is never asked to draw a light tree
with dark colours.

### A screen other than the first

By default the picture is the screen `App.run()` starts with. To see a screen
deeper in the app without editing `main.py`:

```bash
pymobile preview --screen ResultScreen                          # a Screen subclass of the project
pymobile preview --screen ResultScreen --set score=7 --set total=10   # constructor arguments
pymobile preview --screen ResultScreen --args '{"score": 7}'    # the same, as JSON (bash, zsh)
pymobile preview --navigate "Menu.start"                        # press the way there
pymobile preview --navigate "Menu.start, Quiz.answer=42, Quiz.next" --png result.png
```

`--screen NAME` pushes that screen on top of the first one (the class name, or
`module.Class` when two screens share a name). `--set NAME=VALUE`, repeatable,
passes one constructor argument: the value is read as JSON when it parses (`7`,
`true`, `[1, 2]`) and is plain text otherwise (`--set player=Anna`). It needs no
quoting, so it works in every shell — unlike `--args`, whose JSON needs its
double quotes to arrive intact: fine in bash and zsh, but `cmd.exe` and Windows
PowerShell 5.1 strip them. `--args` takes a JSON object for keyword arguments or
a list for positional ones, and a `--set` overrides the same name.

`--navigate` walks a route like a user: each step is `[Screen.]widget` (press it),
`[Screen.]widget=value` (type into a `TextInput`, pick a `Dropdown` option, set a
`Slider`, flip a `Switch` with `true`/`false`) or `<back>`. The optional
`Screen.` prefix asserts where the step is taken. The steps are echoed
(`Menu.start → Quiz`), and a mistake names what is available —
`no screen called 'Resulz'. Did you mean 'Result'?`,
`no widget with id 'strt'. Did you mean 'start'?`. Both flags work with `--png`,
`--ids`, `--text`, and on `pymobile run` (including `--gui` and `--web`, which
then open on that screen) and `pymobile watch`, where every reload lands on the
screen you are working on. The same engine backs the test fixtures — see
[Testing your app](#testing-your-app).

`--png` draws a **mockup**: the tree is laid out with the rules of the
Android renderer (columns stretch their children, rows wrap them,
`Expanded` shares the free space, `Stack` overlays, `Grid` has equal columns,
`Wrap` starts a new line when the row is full, a `SegmentedButtons` bar that
does not fit is cut at the edge because it scrolls)
and painted with Material-style widgets in your theme's colours — raised grey
buttons, underlined text fields, switches, list rows, dialogs over a dimmed
screen and the snackbar — under a status bar and an action bar with the app
name. Sizes are in dp, so spacing and proportions match the device.

It is an approximation, not a screenshot: the phone uses Roboto while the
mockup uses the sans-serif face your machine has, and system colours (the
action bar, the tint of switches) vary between Android versions and vendors —
the mockup uses your `PRIMARY` colour for them. By default the picture is 360
dp wide and as tall as the content, so a long scrolling screen is shown whole;
`--size 360x640` shows exactly the first screenful. From Python:

```python
from pymobile.core.ui.preview import render_mockup
render_mockup(bridge.last_tree, "home.png", theme="dark", height=640)
image = render_mockup(screen.root)       # without a path: a PIL.Image
```

The text PNG (`--text`) uses the first Unicode TrueType face it finds on the system, so
Cyrillic, Greek and accented text render as text rather than boxes. Point
`PYMOBILE_PREVIEW_FONT` at a `.ttf` to choose your own. The canvas is measured
with the real glyph advances of that face, so long lines are never clipped.
Glyphs the chosen face lacks (emoji on a bare Linux container, for instance)
are patched in from a symbol face such as Symbola or Segoe UI Symbol when one
is installed; anything still uncovered is logged once with a hint instead of
failing silently.

The text picture shows the real layout — `Row` children sit side by side,
`ProgressBar` is drawn as a filled bar, `Switch` shows its state:

```text
┌──────────────────────────────┐
│ Pomodoro Focus               │
├──────────────────────────────┤
🎯 Focus
25:00
[░░░░░░░░░░░░░░░░] 0%
(Start)  (Reset)
Vibrate when done  [●] on
└──────────────────────────────┘
```

The same renderer is importable, so tests and notebooks can snapshot a
screen too:

```python
from pymobile.core.ui.preview import render_ascii
print(render_ascii(app.screen.build(), title="Home"))
```

### An interactive window

`pymobile run --gui` opens a real window instead of printing once. Buttons run
their callbacks, switches and text fields feed events back through the same
path the phone uses, and navigation works — a back button appears as soon as
the stack is deeper than one screen. Toasts and vibration show up in the
status strip.

```bash
pymobile run --gui
```

It is built on Tkinter, which ships with CPython, so it costs no dependency.
Trees are patched into the existing widgets when the structure has not
changed, so typing in a field does not lose focus.

### In a browser

`pymobile run --web` serves the same interactive preview over HTTP, which is
what you want on a remote machine, in a container, or when Tk is unavailable:

```bash
pymobile run --web                 # banner prints http://127.0.0.1:8765 to open;
                                   # the server itself listens on all interfaces
                                   # (containers, SSH tunnels, LAN devices)
pymobile run --web --port 9000
pymobile run --web --host 127.0.0.1  # loopback only
```

The page is plain HTML built from the same serialised tree the phone receives,
so it cannot drift from the real renderer. Buttons, switches and text fields
post back to the app, and the page polls for updates, so a timer tick appears
on its own. Widget ids are carried into `data-wid` attributes, so the browser
inspector tells you exactly which widget a node is.

### Hot reload

`pymobile watch` re-renders every time you save:

```bash
pymobile watch                  # text picture on every save
pymobile watch --png ui.png     # write a mockup instead (--text for the text picture)
pymobile watch --interval 0.1   # poll faster
```

Editing an imported helper module counts too, not just `main.py`. A syntax
error prints the exception and keeps watching, so a half-typed line does not
end the session.

```text
• watching /home/me/pomodoro — press Ctrl+C to stop
✓ rendered in 12 ms

• changed: main.py
✓ rendered in 11 ms
```

---

## CLI reference

| Command | Description |
| --- | --- |
| `pymobile init [dir]` | create a project (`-n` name, `-p` package, `-f` force) |
| `pymobile setup-sdk` | install the Android toolchain (`--with-ndk`, `--path`) |
| `pymobile build --native` | build a signed, installable APK (`--minimal-stdlib`, `--no-ssl`, `--abi x86_64`) |
| `pymobile run` | run the app on your machine (`--gui` window, `--web` browser) |
| `pymobile watch` | re-render on every save (`--png`, `--text`, `--ids`, `--interval`) |
| `pymobile preview` | draw a screen as a picture (`--png`, `--size`, `--theme`, `--text`, `--ids`) |
| `pymobile widget-java TYPE` | print the `ViewBuilder.java` branch and Python skeleton for a custom widget type (`-p title -p value:int`, `--out FILE`) |
| `pymobile info` | show the resolved configuration (`--json`) |
| `pymobile doctor` | check environment and project health |
| `pymobile clean` | remove build artifacts |

`run`, `watch` and `preview` also take `--screen NAME` (with `--set NAME=VALUE` or
`--args JSON`) and `--navigate "Menu.start, Quiz.next"` to open on a screen other
than the first —
see [Desktop preview](#desktop-preview).

Global flags `-v` (verbose) and `-c PATH` (project directory) work both before
and after the sub-command.

---

## Extending the framework

The architecture is designed so that additions never touch existing code.

**A new widget** — subclass `Widget`, set `type_name`, override `props()`, then
add a branch to `ViewBuilder.java`:

```python
from pymobile.core.ui.widget import Widget


class Slider(Widget):
    type_name = "Slider"
    __slots__ = ("value", "minimum", "maximum")

    def __init__(self, value=0.0, *, minimum=0.0, maximum=100.0, **kwargs):
        super().__init__(**kwargs)
        self.value, self.minimum, self.maximum = value, minimum, maximum

    def props(self):
        return {**super().props(), "value": self.value,
                "minimum": self.minimum, "maximum": self.maximum}
```

> **The Java branch is not optional — and the build now checks that it exists.**
> `ViewBuilder.java` has one `case` per built-in type, compiled into
> `classes.dex`. A `type_name` with no branch there is drawn as a red
> `[BarChart: no native renderer]` placeholder on the phone, while the desktop
> and browser previews print `<BarChart>` and look perfectly fine. Because the
> packaged `classes.dex` is prebuilt, a branch added to `ViewBuilder.java`
> reaches a phone only through a rebuild of that dex
> (`PYMOBILE_BUILD_JAVA=1 pymobile build --native`, which needs the Android
> SDK's `d8`). What makes a gap impossible to miss:
>
> - **`pymobile build --native` stops** when the `classes.dex` it is about to
>   package has no branch for a widget type your project defines (a class that
>   derives from a framework widget and sets `type_name = "BarChart"`) or
>   registers with `register_widget_type("BarChart")`. It asks the dex itself, so
>   a stale prebuilt dex, a forgotten rebuild and a typo in the `case` label are
>   all caught — before the APK is signed. The error names the three ways out;
> - `pymobile build` (any build) warns about a defined type nothing has declared,
>   and a native build warns about types declared preview-only;
> - `pymobile widget-java BarChart -p title -p value:int` prints everything you
>   need to write: the Python widget, the `case`, the `buildBarChart(...)` method
>   (it reads your props and shows them, so the widget is visible at once), the
>   `updateNode` branch, and the rebuild command;
> - on the device the renderer logs `no native renderer for widget type "BarChart"`
>   (`adb logcat -s pymobile`), and `App` logs the same once per unknown type on
>   the first frame sent to a native bridge (desktop previews stay quiet);
> - `unknown_types(screen.to_dict())` returns the offending names, so a test can
>   assert the tree contains nothing the renderer would drop:
>   `assert unknown_types(Shell().to_dict()) == frozenset()`.
>
> Three ways out of the build error. **Compose** the widget from existing ones —
> a `BarChart` made of `Row`, `ProgressBar` and `Expanded` needs no Java at all,
> and is the safest route. **Add the branch** with `widget-java` and rebuild the
> dex. Or declare it **preview-only**:
> `register_widget_type("BarChart", android=False, web=True)` — the previews draw
> it, the phone does not, and the build only warns. Declaring a type without
> `android=False` (`register_widget_type("BarChart")`) is a promise that the Java
> branch exists, and the build holds you to it.

```python
from pymobile import register_widget_type, unknown_types

register_widget_type("Slider")             # case "Slider" exists in ViewBuilder.java
assert unknown_types(screen.to_dict()) == frozenset()   # nothing the renderer drops
```

`Wrap` is an example of a widget added the full way: `class Wrap(Container)` in
`layout.py`, a `WidgetCapability` row in `registry.py`, a branch in each of the
four renderers (`ViewBuilder.java` + a rebuilt `classes.dex`, `web.py`, `gui.py`,
`preview.py`/`mockup.py`) and tests for all of them.

**A new Android API** — add a method to `Bridge`, implement it in
`AndroidBridge` and `StubBridge`, wrap it in a small class under `core/api/`.

**A new build stage** — write a method and add one line to
`BuildPipeline.run()`; timing and logging come for free.

**A new platform** — implement `Bridge` once; nothing else changes.

---

## Limitations

- **Two ABIs: arm64-v8a (phones) and x86_64 (the emulator)**, one per APK —
  `pymobile build --native --abi x86_64`. 32-bit devices are not supported,
  and there is no iOS backend.
- **APK size is about 16.6 MB** (11.7 MB with `--minimal-stdlib --no-ssl`),
  dominated by the interpreter and standard library.
- **Android 7.0 (API 24) minimum.** The python.org build of CPython 3.14
  requires it (earlier pymobile releases declared API 21, but the app could
  not start there). A `min_sdk = 21` left in an older `pymobile.toml` still
  builds: the APK declares 24 and the build prints a warning.
- The renderer covers the components documented here; more are being added.
  A custom widget with a new `type_name` is not one of them until you add a
  branch to `ViewBuilder.java` and rebuild the dex — without it the widget is a
  placeholder on the phone, so `pymobile build --native` stops, and the
  framework also warns on the first frame and in the device log (see
  [Extending the framework](#extending-the-framework)).
- Translation catalogues are flat: `"stats.balance"` is one key, and nested
  JSON sections need `flatten=True` (see [Languages](#languages)).

---

## Known issues

There are currently no open known issues. If you hit something new, report
it on [GitHub Issues](https://github.com/Maksum867/py-mobile/issues) with a
reproducer (`main.py` + `pymobile.toml`) and the device or platform. Bug
reports with a regression test land faster.

---

## Versioning and deprecation policy

PyMobile follows [Semantic Versioning 2.0.0](https://semver.org/):
`MAJOR.MINOR.PATCH`. This page is the promise; the
[changelog](https://github.com/Maksum867/py-mobile/blob/main/CHANGELOG.md) is
where each release keeps it.

### What the public API is

Everything this policy protects:

- every name importable from `pymobile` (`pymobile.__all__`) and from `pymobile.testing`;
- the modules and functions this README documents by path
  (`pymobile.core.ui.preview.render_ascii`, `pymobile.core.bridge.StubBridge`,
  `pymobile.core.validation` …);
- the `pymobile` command — sub-commands, flags and exit codes;
- the keys of `pymobile.toml`;
- the serialised widget tree a renderer receives: `type`, `id`, `props`, `style`,
  `children`, and the type names and props of the built-in widgets;
- the Python versions in the package classifiers (dropping one is a minor-release
  change, announced in the changelog).

Not covered: names that start with an underscore; anything the README does not
document; how a preview *looks* (pixels, fonts, the text picture's exact layout);
log and warning wording; build timings; the internals of `classes.dex` and
`libpymobile.so` (only their protocol with Python is API).

### What each release may contain

| Release | Contains |
| --- | --- |
| **PATCH** (`0.9.1`) | Bug fixes only. No API change, no new deprecation. |
| **MINOR** (`0.10.0`) | New features that keep old code working, and **new deprecations**. Before 1.0 a minor release may also make a breaking change — it is listed under *Changed* or *Removed* in the changelog. |
| **MAJOR** (`2.0.0`) | Removal of what was deprecated, and other incompatible changes. |

Until **1.0**, PyMobile is alpha (see the note at the top): the API can change
between minor releases. What does not change is *how* things go away — see the
next section. From 1.0 on, an incompatible change to the public API happens only
in a major release.

### The deprecation cycle

Nothing in the public API is removed without going through three steps:

1. **Announce.** A minor release adds the replacement and marks the old spelling
   deprecated. Using it emits a `PyMobileDeprecationWarning` that names the
   replacement and the release that will remove it; the changelog lists it under
   *Deprecated*; the documentation stops recommending it.
2. **Keep.** The deprecated spelling keeps working, unchanged, for **at least one
   full minor release after the one that announced it**: deprecated in `0.9`, it
   still works — and warns — in `0.10`.
3. **Remove.** Only after that, in the release the warning named. From 1.0 on,
   removals happen only in a major release, so a deprecation announced in `1.3`
   is removed in `2.0` at the earliest.

The one exception is a security or data-loss bug, which may change behaviour at
once; the changelog says so under *Security*.

### Seeing the warnings

`PyMobileDeprecationWarning` is a `DeprecationWarning`: Python shows it for code
you run yourself (a `main.py` run as a script — the message carries the file and
line of *your* call) and pytest lists it in its warnings summary. To find every
use before an upgrade, or to keep them out for good, turn it into an error in CI:

```bash
pytest -W error::pymobile.deprecation.PyMobileDeprecationWarning
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
filterwarnings = ["error::pymobile.deprecation.PyMobileDeprecationWarning"]
```

### Deprecated now

| Deprecated | Use instead | Since | Removed in |
| --- | --- | --- | --- |
| `max=` on `Slider`, `Stepper`, `ProgressBar`, `ProgressText`, `RatingBar`; `min=` on `Slider`, `Stepper` | `maximum=`, `minimum=` | 0.9.0 | 1.0.0 |
| `maxlength=` on `TextInput` | `max_length=` | 0.9.0 | 1.0.0 |
| `on_change=` on `Dropdown`, `SegmentedButtons`, `BottomNavigation` | `on_select=` | 0.9.0 | 1.0.0 |

Two spellings of one argument are permanent the day the API is frozen, which is
why these are retired *before* 1.0 and why new API gets one name only. The
alternative spellings that remain — `Storage.exists()` next to `contains()`,
`RatingBar(value=…)` next to `rating`, `then(on_success=…)` next to `on_done` —
are documented and are **not** deprecated.

### Pinning

- Before 1.0: `pymobile-framework~=0.9.0` accepts patch releases only; move to
  the next minor when you have read its changelog.
- From 1.0: `pymobile-framework>=1,<2` is safe.

---

## FAQ

**Why doesn't my UI update?**
It should: assigning to a widget property redraws the screen by itself. If the
*structure* changed — rows added or removed — call `self.refresh()` to rebuild
through `build()`. See [Updating the screen](#updating-the-screen).

**Why does `pymobile run` show no window?**
By default it runs your logic through the stub bridge without native views,
which is what makes apps testable without an emulator. Use `pymobile run --gui`
for a real, clickable window, `pymobile preview` for a text picture, or
`pymobile watch` to re-render on every save.
See [Desktop preview](#desktop-preview).

**Why doesn't a 404 raise an exception?**
Because it is a valid HTTP response. Use `response.ok` or
`response.raise_for_status()`.

**Why is no permission dialog shown?**
Either the permission is missing from `pymobile.toml`, or you have already
denied it twice and Android has blocked further prompts. See
[Permissions](#permissions).

**Why don't notifications appear on the device?**
Two things must both be true: (1) `POST_NOTIFICATIONS` is in `pymobile.toml`,
and (2) the permission is requested at runtime after the screen is visible.
The default APK already creates the notification channel. Also check that
notifications are enabled in *Settings → Apps → Your App → Notifications*.
See [Notifications](#notifications).

**Why doesn't my button respond?**
Check `enabled` — a disabled button ignores taps.

**Why does `push()` raise an error?**
The same screen object cannot be pushed twice. Create a new instance.

**Where did my `print()` output go?**
To logcat: `adb logcat -s pymobile.stdout`.

**How do I make the APK smaller?**
Build with `--minimal-stdlib --no-ssl` if the app allows it, trim files with
`exclude`, and list a single ABI. `optimize = true` (bytecode instead of
sources) also helps, but only takes effect when the build runs on Python 3.14 —
the device's version; elsewhere it is skipped with a warning, which is why new
projects start with `optimize = false`.

**Can I keep the config in `pyproject.toml`?**
Yes, under `[tool.pymobile]`. If both files exist, `pymobile.toml` wins.

**Do I need Android Studio?**
No. `pymobile setup-sdk` downloads only the command-line tools it needs.

---

## Contributing

```bash
git clone https://github.com/Maksum867/py-mobile.git
cd py-mobile
pip install -e ".[dev]"

pytest                  # unit tests
ruff check pymobile     # linting
mypy pymobile           # strict type checking
```

To rebuild the native artifacts from source (requires
`pymobile setup-sdk --with-ndk`):

```bash
# macOS / Linux
PYMOBILE_BUILD_JAVA=1 PYMOBILE_BUILD_JNI=1 pymobile build --native --clean
```

```powershell
# Windows PowerShell
$env:JAVA_HOME = "C:\Users\You\.andro\jdk-17.0.13+11"
$env:PYMOBILE_BUILD_JAVA = "1"
$env:PYMOBILE_BUILD_JNI = "1"
pymobile build --native --clean
```

A change that removes or renames public API goes through the
[deprecation cycle](#the-deprecation-cycle): keep the old spelling, call
`pymobile.deprecation.warn_deprecated(...)`, add a test with
`pytest.warns(PyMobileDeprecationWarning)` and a *Deprecated* changelog entry
(the suite treats every other `DeprecationWarning` as an error, so the framework
itself cannot keep using an old spelling by accident).

Issues and pull requests are welcome.

---

## Documentation

- **[CHANGELOG.md](https://github.com/Maksum867/py-mobile/blob/main/CHANGELOG.md)** — release history
- **[Versioning and deprecation policy](#versioning-and-deprecation-policy)** — what may change, and how much notice you get

## License

MIT — see [LICENSE](https://github.com/Maksum867/py-mobile/blob/main/LICENSE).