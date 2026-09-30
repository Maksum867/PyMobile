# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [semantic versioning](https://semver.org/).

## [0.8.3] — 2026-09-30

### Added

- **`Wrap`** — a flow layout: children side by side at their natural size, a
  new line when the row is full (`spacing`, `run_spacing`, `align`). It exists on
  every renderer — `ViewBuilder.java` (a `FlowLayout` view group) and the
  rebuilt `classes.dex`, the browser and Tk previews, the mockup and the text
  picture — and all of them break lines by one rule
  (`pymobile.core.ui.flow.flow_lines`, mirrored by `ViewBuilder.FlowMath`).
  Closes **#LAY-05**.
- **`SegmentedButtons(wrap=True)`** — flow the options onto several lines instead
  of scrolling them (see *Changed* for the default).
- **`pymobile.testing` and a pytest plugin** — registered by a `pytest11` entry
  point, so the fixtures exist as soon as pymobile is installed:
  `pymobile_session` (app + stub bridge + driver, torn down afterwards),
  `pymobile_app`, `pymobile_bridge`, `pymobile_driver` and `pymobile_snapshot`
  (golden-file checks of a screen's text picture; `--pymobile-update-snapshots`
  or `PYMOBILE_UPDATE_SNAPSHOTS=1` to accept a change; a missing snapshot is a
  failure when `CI` is set). The store lives in `tmp_path`; the bridge and the
  language are restored after every test. `pymobile.testing` itself imports
  without pytest (`app_session`, `Driver`, `SnapshotChecker`).
- **`pymobile.core.driver.Driver`** — operates a running app like a user:
  `open("ResultScreen", score=7)`, `press`, `long_press`, `change` (type, pick,
  set, switch), `back`, `find`, `navigate("Menu.start, Quiz.answer=42,
  Quiz.next")`, `Driver.attach()` for an app that `main()` created. Mistakes are
  `PyMobileError`s with a hint (*Did you mean 'start'?*).
- **`--screen NAME`, `--set NAME=VALUE` (or `--args JSON`) and
  `--navigate "Menu.start, Quiz.next"`** on `pymobile preview`, `run` (text,
  `--gui`, `--web`) and `watch`, so a screen deep in the app can be previewed,
  screenshotted and hot-reloaded without editing `main.py`. `--set` needs no
  quoting, which JSON does in `cmd.exe` and Windows PowerShell. Closes the
  "preview renders only the first screen" gap.
- **`Validator(rules, messages=…)`** and **`DEFAULT_MESSAGES`** — validator
  messages are no longer hard-wired English. A message comes from `messages=`
  (`"required"` or `"email.required"`; a template or a callable), else from the
  translation catalogue under `validation.<rule id>`, else the English default,
  which is unchanged byte for byte. An unknown rule id is an error at
  construction. `RuleMessage` (a `str` that knows its rule) lets the bare rule
  functions keep returning plain strings.
- **`Screen.is_current`** — whether the screen is on display (`app.screen is
  self`, without the private `_app` and without raising). Kept by the navigator,
  so it is also safe to read from a worker thread.
- **`Screen.title_key`** (and `Screen(title_key=…)`) — the title is translated
  from the catalogue before every `build()`, so it follows a language switch.
- **`pymobile widget-java TYPE [-p NAME[:TYPE]]…`** — prints the Python widget
  skeleton, the `case` and `build<Type>()` method for `ViewBuilder.java`, the
  `updateNode` branch and the rebuild command for a custom widget type.
- **`PyMobileDeprecationWarning`**, `pymobile.deprecation.warn_deprecated()` — one
  category and one helper for every deprecation (see *Deprecated*).
- **`WidgetParentError`** — what `Container.add` raises for a widget that already
  has a parent; it is a `PyMobileError` **and** a `ValueError`. Closes **#CNT-02**.
- **`Avatar(..., is_image=False)`** — `is_image` is three-valued (`None` guesses,
  `True`/`False` decide), so a path-shaped string can be initials. Closes
  **#AVT-04**.
- **Versioning and deprecation policy** in the README: what the public API is,
  what each release may contain, the announce → keep (at least one minor
  release) → remove cycle, the list of what is deprecated now.

### Changed

- **`pymobile build --native` stops** when the `classes.dex` it is about to
  package has no branch for a custom widget type the project defines (a class
  deriving from a framework widget with `type_name = "…"`) or registers with
  `register_widget_type("…")`. It asks the dex's string table, so a stale
  prebuilt dex, a forgotten `PYMOBILE_BUILD_JAVA=1` and a typo in the `case`
  label are caught before the APK is signed. A structural `build` still warns;
  a type declared `android=False` only warns. The scan now looks at classes
  (AST) instead of any `type_name = "…"` line, so an unrelated class attribute
  is no longer reported.
- **`SegmentedButtons` scrolls instead of squeezing.** On the device the bar is a
  `HorizontalScrollView` holding natural-width segments (and scrolls to the
  selected one); a five-segment bar of long labels at 360 dp used to wrap
  "Біологія" into a column of letters. Bars that fit look as before. The
  in-place update now rebuilds the bar when the labels change (a language
  switch used to leave the old labels on the phone). The mockup, browser and
  text previews follow.
- **`Screen.to_dict()` builds the tree before it reads the title**, so the frame
  after a language switch carries the new title.
- The README's *Testing*, *Desktop preview*, *CLI reference*, *Extending* and
  *Input validation* sections describe the above; examples use the canonical
  argument names.

### Deprecated

Removed in **1.0.0**; each warns with `PyMobileDeprecationWarning`, naming the
replacement:

- `max=` on `Slider`, `Stepper`, `ProgressBar`, `ProgressText`, `RatingBar` and
  `min=` on `Slider`, `Stepper` — use `maximum=` / `minimum=`.
- `maxlength=` on `TextInput` — use `max_length=`.
- `on_change=` on `Dropdown`, `SegmentedButtons`, `BottomNavigation` — use
  `on_select=`. (`on_change` stays the canonical name on `TextInput`, `Slider`,
  `Stepper`, `RatingBar` and the pickers: they report a value, not a choice.)

### Fixed

- **The `targetSdk >= 33 without POST_NOTIFICATIONS` build warning no longer fires
  for apps that never notify.** It now runs after the sources are collected and
  only when they post a notification (`app.notify`, `app.notifications.notify`,
  `Notifications(...)`, a syntactic scan that ignores comments, docstrings,
  `tests/` and virtualenvs); the message names the file and line.
- **`App.current()` no longer races.** Publishing the app in `run()`, withdrawing
  it in `stop()` (a check *and* a write) and reading it are one lock, so a
  `stop()` racing another app's `run()` cannot withdraw the newer app, and a
  worker thread reads a consistent value. Closes **#APP-09**.
- **The packaged `classes.dex` was stale.** It had been built before
  `ViewBuilder.buildUnknown`, so the red "no native renderer" placeholder the
  README promised was not on the device. It is rebuilt (JDK 17, build-tools
  35.0.0, through `NativeBackend._compile_java_from_source`, `--min-api 24`).
  It has not been run on a physical device: the Java is compiled against
  `android.jar` 35, and the flow-layout arithmetic was checked on a JVM against
  an independent model.

### Removed

- The Known-issues rows **#AVT-04**, **#APP-09**, **#CNT-02** and **#LAY-05**
  (fixed above); **#WAT-03** stays open.

## [0.8.2] — 2026-09-28

A full-project review found twelve defects; all twelve are fixed here on top of
the three below. Three failures that were quiet by design are now loud, and the
two pieces of behaviour behind them are documented.

### Added

- **`AlertDialog.acknowledge()`** — the public twin of the OK button, on par
  with `ConfirmDialog.confirm()`/`.cancel()`; `dismiss()` stays as an alias.
  `confirm()`, `cancel()` and `acknowledge()` now fire their callback **once per
  showing**: calling one twice, or `confirm()` then `cancel()`, no longer runs
  the handler twice (on a device the button is gone after the first tap).
  `open()` resets the outcome, so the same dialog can be shown again.
- **`ProjectConfig.exclude_only`** and **`pymobile.core.config.DEFAULT_EXCLUDE`**
  — the built-in exclude patterns are now visible and opt-out-able.
- **`Validator.normalize()`** (and the `RuleSet` type alias) — expands
  `{"email": "email"}` into `{"email": ["email"]}` without writing a Validator.
- **`DataTable` rows** are checked in the constructor exactly as `add_row()`
  checks them; `List.item_count` is checked on assignment as well as in the
  constructor.

### Fixed

- **`exclude` no longer drops the built-in patterns.** A project with
  `exclude = ["docs/**"]` used to ship `build/` — including the previous APK —
  and `.git/`, `__pycache__/` and `tests/` into the package. `exclude` now
  **adds** to the defaults; `exclude_only = true` replaces them, and the build
  warns when `output_dir` would ship itself.
- **`None` in a text prop renders as an empty string, not the word "None".**
  `TextInput(None)`, `ListTile(None, subtitle=None)` printed `None` in all four
  previews while the phone showed nothing; they go through
  `pymobile.core.ui.contract.text_value()` now, like `Label` already did.
- **`Switch`/`Checkbox` serialise `checked` as a real boolean.** A `"false"`
  string (a value read back from JSON or the store) was truthy in Python and
  `false` to the device's `optBoolean`, so the preview and the phone disagreed.
  The constructor and the setter now parse `"false"`/`"no"`/`"off"`/`"0"` as
  off and everything else by truthiness.
- **A misspelled keyword argument on a built-in widget is named.** `Label(colr=…)`
  was stored as an extension prop the renderers ignore and rendered without
  colour, silently; `to_dict()` now logs
  `Label has no prop 'colr': it was stored as an extension prop, which the
  renderers ignore. Did you mean 'color'?` once per (class, prop), and a
  misspelled callback (`Button(on_pres=…)`) raises a `TypeError` that points at
  the argument. Extension props on a custom `type_name` are untouched.
- **`App.run()` after `App.stop()` raises instead of half-starting.**
  Restarting a stopped app logged "starting", ran jobs against a dead bridge and
  left the UI frozen; it now raises
  `PyMobileError("The application has already been stopped")` with the fix in
  the hint ("create a new App").
- **`HttpSecurityPolicy(allowed_hosts=["api.example.com:8443"])` matches.**
  The port was stripped from the URL before comparing, so a policy with an
  explicit port blocked every request to its own host. Bare hosts still match
  any port; the default port of the scheme is used when the URL omits one; the
  block message lists what is allowed; `allowed_hosts=[]` now raises
  `ValueError` instead of blocking everything with the same message as a wrong
  host.
- **`Validator({"email": "email"})` validates instead of iterating the string.**
  A bare string, mapping or callable rule was treated as a sequence of rules, so
  `"email"` was checked one character at a time and every value passed. One rule
  and a list of rules now behave the same; anything else raises
  `ValueError("rules for 'email' must be a rule or a sequence of rules, got
  'int'")` at construction.
- **`toggle()` on a disabled `Switch`/`Checkbox` no longer flips it** (and no
  longer fires `on_toggle`): the visual state said "disabled" while the value
  changed anyway.
- **`List.item_count` validates on assignment.** `lst.item_count = -5` was
  accepted and the next `refresh()` emptied the list; a negative value raises
  `ValueError`, a non-int `TypeError`, and shrinking the list drops the extra
  rows on the next `refresh()`.
- **`DataTable(headers, rows)` checks the row length like `add_row()` does.**
  The constructor padded a short row (fine) but silently kept extras in one
  place and rejected them in the other; both raise
  `ValueError("row has 3 cells but the table has 2 column(s)…")` now, short rows
  are padded, and `None` cells become empty strings.
- **`Storage.increment()` keeps a numeric string, and `Storage.update()`
  demands a default for a missing key.** `store.set("n", "5"); increment("n")`
  returned `1` and overwrote the `5`; a numeric string is parsed now, and a
  genuinely non-numeric value is reported with a warning. `update()` on a
  missing key used to hand `None` to the caller's function (which then raised
  `TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'` from
  inside their own lambda); it raises
  `PyMobileError("Storage key 'cart' does not exist", hint="pass default=…")`
  unless `default=` is passed — `default=None` is still valid.

### Changed

- **`flatten_catalogue()` and `flatten=True`** on `Translations.load()`,
  `load_dict()`, `load_file()` and `load_dir()`. The lookup is flat — a dot is
  part of the key — so a catalogue written as sections silently rendered bare
  keys. Loading such a file now warns, naming the nested objects, and the
  opt-in `flatten=True` turns `{"stats": {"balance": …}}` into `"stats.balance"`
  without touching the default behaviour. A nested object whose keys are all
  CLDR quantity names is still read as plural forms.
- **`register_widget_type()` / `unregister_widget_type()` / `declared_types()` /
  `known_types()` / `is_known()` / `unknown_types()`** in
  `pymobile.core.ui.registry`; `register_widget_type`, `unregister_widget_type`
  and `unknown_types` are also exported from `pymobile` itself. A custom `type_name` with no branch in
  `ViewBuilder.java` is drawn as an empty view on the phone, and the registry
  is what makes that visible: `unknown_types(screen.to_dict())` reports the
  types a renderer cannot draw (a test-sized version of the check), and
  declaring a type stops the new warnings.
- **`Bridge.native_widgets`** — `True` on `AndroidBridge` and `JNIBridge`,
  which hand the tree to the Java renderer; previews leave it `False`.

### Fixed

- **A redraw requested while a frame is being built no longer recurses.**
  A shell that called `refresh()` from `build()` (or from a lifecycle hook
  running during the frame) ran `build()` inside itself until the interpreter
  raised `RecursionError`. `App.render()` is now non-re-entrant: the request is
  recorded and one more frame is drawn when the current one finishes (at most
  eight in a row, after which the loop is stopped and reported).
- **A custom widget type is no longer dropped in silence.** `App` logs
  `widget type 'BarChart' has no branch in the native renderer` once per type
  when a frame goes to a native bridge, `BuildPipeline` warns about
  `type_name = "…"` values nothing declares before the APK is written, and
  `ViewBuilder.java` draws a red `[BarChart: no native renderer]` placeholder
  and logs `no native renderer for widget type` instead of an empty view.
- **A `refresh()` that cannot repaint anything now says so.** It used to be a
  no-op for a screen that is not on the navigator stack (a tab embedded in
  another screen's tree); the first one is logged with the fix — rebuild the
  shell that owns the frame.

### Documentation

- README, *Configuration*: `exclude` **adds** to the built-in patterns (listed
  in the table, with `exclude_only` and `DEFAULT_EXCLUDE`) — the old example
  shipped `build/` into the APK.
- README, *Languages*: new subsection **"Keys are flat — a dot is part of the
  key"** — the flat-key rule, the shape that misses, and `flatten=True`.
- README, *Screens and navigation*: new subsection **"Screens as tabs: one
  shell owns the frame"** — only `navigator.current` is rendered, what
  `refresh()`/`self.on()`/`self.app` do for a screen that is not on the stack,
  and the shell-with-tabs pattern that works.
- README, *Extending the framework*: the Java branch requirement is called out
  with the failure it prevents (empty view on the phone, fine in the preview),
  the four checks that make it visible, and the note that the prebuilt
  `classes.dex` has to be rebuilt for a renderer change to reach a phone.
  Also added to *Limitations*.

## [0.8.1] — 2026-09-27

### Added

- **`ConfirmDialog.confirm()` / `.cancel()`** — public test helpers that do
  exactly what the renderer does on a button tap: close the dialog, then fire
  `on_confirm` / `on_cancel` (renamed from the private `_accept` /
  `_decline`). On par with `ListTile.swipe()` and `Snackbar.press()`, so tests
  drive the dialog without a bridge or event loop.

### Changed

- **`Screen.app` is a property, not a `None`-able attribute.** It returns the
  running `App` and, once the screen leaves the stack (`pop()`, `replace()`,
  `reset()`, app shutdown), raises
  `PyMobileError("screen '<title>' is no longer on the stack",
  hint="grab app = self.app before calling pop()")` instead of silently
  returning `None`. `App | None` is gone from the public types: a callback
  that may outlive the screen grabs `app = self.app` before calling `pop()`,
  and a stale callback now fails immediately with an actionable error instead
  of a cryptic `AttributeError` on `None` a few lines later.
- **`on_press` accepts callbacks that return a value** (`Callable[[],
  object]` instead of `Callable[[], None]`) on `Button`, `Chip`,
  `RadioButton`, `Link` and `ListTile`: `Button("Back", on_press=self.app.pop)`
  type-checks as is — no `lambda: self.app.pop()` wrapper.
- **`pymobile init` templates** — the generated README documents
  `pymobile build --native` (real, signed, installable APK; `pymobile
  setup-sdk` first) and marks the plain `build` as a non-installable
  structural check; the generated config adds `docs/**` to `exclude` so
  documentation is never shipped into the APK; the generated `main.py` drops
  the now-dead `if self.app is not None` guards.

### Documentation

- APK signature schemes corrected to `v2+v3`: the toolchain signs with the
  v2+v3 schemes, while the docs described `v1+v2+v3` (README, two places,
  plus the 0.1.0 changelog entry).
- The README navigation example uses `on_press=self.app.pop` instead of
  `on_press=lambda: self.app.pop()`.

## [0.8.0] — 2026-09-26

### Added

- **Typed `find()`.** `screen.find("name", TextInput)` returns
  `TextInput | None` for editors and mypy; `screen.get(id, Cls)` raises
  `WidgetNotFoundError` (a `LookupError`, with a *did you mean* hint) instead
  of returning `None`; `widget.find_all(Cls)`. A widget of another class raises
  `WidgetTypeError` (a `TypeError`). Closes #FND-07.
- **Numbers, dates and money by language:** `format_number`, `format_percent`,
  `format_currency`, `format_date`, `format_time`, `format_datetime` for 16
  languages and their regional variants (grouping, decimal separator, symbol
  placement, month names in the right grammatical case), no dependencies.
  Catalogue placeholders take the same formats: `{sum:currency:UAH}`,
  `{day:date:long}`, `{n:number:2}`, `{p:percent}`, `{t:time}`. Closes #I18-08.
- **List gestures**, built into the renderer (no AndroidX):
  - `ListTile(on_swipe_left=…, on_swipe_right=…)` — drag a row sideways past a
    third of its width (or fling it) and it slides out; `swipe_left_color` /
    `swipe_right_color`; `tile.swipe("left")` in tests.
  - `List(on_refresh=…)` — pull to refresh with a spinner that keeps turning
    while a returned job / HTTP future runs; `refreshing`, `pull_to_refresh()`.
  - `List.scroll_to(index, animated=True)` now really scrolls the enclosing
    `ScrollView` to the row (it only built the rows before).
- **Snackbar:** `app.snackbar("Deleted", action="Undo", on_action=restore)` —
  a bottom bar with one action that hides itself (4 s, 7 s with an action),
  can be swiped away and survives navigation; `app.current_snackbar`.
- **x86_64 emulator builds without the NDK:** the prebuilt JNI bridge ships for
  x86_64 too; `pymobile build --native --abi x86_64` produces
  `my-app-1.0.0-x86_64.apk`.
- **`preview --png` draws a mockup of the screen** as the phone lays it out —
  Material-style buttons, fields, switches, list rows, dialogs, the snackbar,
  theme colours, dp sizes — instead of the rasterised text picture
  (`--text` keeps that). `--size 360x640`, `--theme dark`; `watch --png` too.
  From Python: `render_mockup(tree, "home.png")`.
- The Tk and browser previews show the snackbar, ⟵ / ⟶ swipe buttons on rows,
  a *Pull to refresh* button, and the browser scrolls for `scroll_to()`.

### Changed — behaviour

- **Callbacks run on the UI side.** `set_interval` / `set_timeout` callbacks,
  `app.run_job(...).then(...)` and `app.http.*_async(...).then(...)` now run
  where UI state lives — the event-loop thread on a device, the Tk thread in
  `run --gui`, under the UI lock elsewhere — and draw one frame. They used to
  run on background threads and race with event handling. Blocking timer work:
  `set_interval(..., background=True)`. A standalone `HttpClient()` /
  `JobManager()` keeps the old behaviour. See *Threads and the UI* in the README.
- **`app.dispatch(fn)` runs `fn` on the event-loop thread** on a device (the
  loop is woken through the bridge); it used to run on the calling thread.
- **Validator:** a field without `optional` is validated even when empty —
  `["email"]` rejects `""`, `optional` is no longer a no-op. `required` rejects
  whitespace-only strings; `integer` rejects `"4_2"`; `number` rejects `nan`/`inf`.
  `{"matches": "password"}` fails for an empty confirmation while the password
  is filled.
- **Plurals follow CLDR rules of the catalogue's language** (Polish 21 →
  `many`, Ukrainian 21 → `one`, French 0 → `one`, …) instead of guessing from
  which forms the catalogue contains. `plural_category(n, lang)` is public.
- **Assigning any public widget attribute redraws** (`style`, `placeholder`,
  `Badge.color`, `Avatar.image`, `ProgressText.label`, …), not only
  text/value/checked/visible/enabled. Unchanged values still render nothing.
- **`List` loads more rows on scroll.** It showed `visible_count` rows and
  never more; the renderer now sends `load_more` when the last row becomes
  visible and the next page is appended in place (scroll position kept).
  `loaded`, `has_more`, `load_more()`, `scroll_to()`; the previews show a
  *Load more* button.
- **`RadioGroup` tracks buttons by position:** duplicate labels work;
  `selected_index`, `select_index()`.
- **Android 7.0 (API 24) is the minimum.** The python.org build of CPython
  3.14 that pymobile embeds targets API 24 (`libpython3.14.so` needs
  `preadv`, `pwritev`, `lockf`), so APKs that declared API 21 installed on
  Android 5–6 and died at launch. New projects say `min_sdk = 24`; a
  `min_sdk = 21`–`23` in an existing `pymobile.toml` still builds, but the APK
  declares 24 and the build warns (`ProjectConfig.effective_min_sdk`,
  `RUNTIME_MIN_SDK`). `pymobile info` shows both values.
- New projects start with `optimize = false` (bytecode needs a Python 3.14
  host); the template no longer calls `self.app.render()`.

### Fixed — threads and lifecycle

- An exception in `JobHandle.then(on_success)` was swallowed, set `result` to
  None and skipped `on_error`. It is logged with its traceback and passed to
  `on_error`; the job's result is kept.
- `JobHandle.cancel()` marked a still-running job `done`.
- A `TypeError` raised inside an app's handler was logged as "value '' is not
  valid for a Button" without a traceback.
- `replace()` / `reset()` with a screen whose `build()` fails left an empty
  stack; the new screen is now built before the old one is taken down.
- Every `App()` stayed subscribed to the global `translations` forever.

### Fixed — widgets and API

- `Style(padding=8)` was accepted and crashed later in `to_dict()`
  (`AttributeError: 'int' object has no attribute 'to_list'`). `padding` and
  `margin` take `8` (all sides) or `(left, top, right, bottom)` besides
  `EdgeInsets`; an ambiguous pair, a negative value or another type raises at
  construction with an example of the right spelling.
- `Link(url=...).press()` always crashed (`ModuleNotFoundError`).
- `TextInput(value=..., max_length=n)` kept an over-long initial value.
- Conflicting aliases (`Slider(minimum=0, min=10)`) raise instead of silently
  picking one; `RatingBar.value` is settable.
- Auto-ids of widgets created outside `build()` collided with ids inside it.
- `StubBridge.toast()` / `PlatformBridge.toast()` default to `long=False`;
  `app.toast()` is documented.

### Fixed — events, HTTP, storage, build

- `app.off(event, self.handler)` never removed a bound method; `App.off()`
  added. `Subscription.cancel()` removed other subscriptions of the same
  function too.
- `HttpSecurityPolicy(allowed_hosts=...)` compared host names
  case-sensitively and accepted a bare string.
- A store file named after the package with dots (`com.example.app.json`) was
  ignored; it is adopted when the dashed name is missing. A broken
  `pymobile.toml` that made `App()` fall back to the shared
  `org.pymobile.app` store is now a warning, not a debug message.
- The incremental build ignored the framework: after upgrading pymobile,
  `build --native` answered "up to date" with an APK containing the old
  renderer. The fingerprint now covers the framework version, the dex, the JNI
  bridge and `PYMOBILE_BUILD_JNI`.

### Fixed — JNI bridge

- **`pymobile_jni.c` did not compile** (`event_free` used before its
  definition), so `PYMOBILE_BUILD_JNI=1` always fell back to the prebuilt
  bridge, and the prebuilt `libpymobile.so` predated the 0.7.x lifecycle
  fixes (no `pythonIsInitialized`, stop flag never reset). Fixed and rebuilt.
- `value != ""` compared pointers; a failed `GetStaticMethodID` left a pending
  exception before the next JNI call (undefined behaviour, a CheckJNI abort).
- New `wake()` pushes the dispatch wake-up straight into the event queue;
  older bridges fall back to a marker passed through `Native.render()`.

### Documentation

- New *Threads and the UI* section; `HttpSecurityPolicy`, `app.toast()`,
  Validator empty-value rules, CLDR plurals, `List` paging, `RadioGroup`
  indices, the desktop store file name.
- New sections: typed `find()`, *Numbers, dates and money*, *Swipe and pull
  to refresh*, *Snackbar*, *Running on the emulator (x86_64)*, the PNG mockup.
- Removed the fixed #PLG-12, #FND-07 and #I18-08 from *Known issues*; the FAQ has its heading back;
  the HTTP cache example no longer imports twice.

## [0.7.4] — 2026-09-26

### Security

- **HTTP redirects bypassed `HttpSecurityPolicy`:** only the first URL was
  checked, so `https://allowed/redirect` → `http://localhost/secret` succeeded.
  Every redirect hop is now validated against the policy.
- **HTTP cache leaked responses between accounts:** the key was the URL only.
  It now includes a fingerprint of `Authorization`/`Cookie`/`X-API-Key`, and the
  cache is bounded (`max_entries`, oldest evicted first).
- **Signing passwords on the command line:** `PYMOBILE_KS_PASS` /
  `PYMOBILE_KEY_PASS` are read from the environment and handed to `apksigner`
  as `env:` references, so they no longer appear in `ps`. Passing `--ks-pass`
  still works but prints a warning.
- **`allowBackup` is now `false`** by default (the private store often holds
  tokens). Opt in with `allow_backup = true` in `pymobile.toml`.

### Fixed — Android

- **The prebuilt `classes.dex` was stale since v0.3.0.** It is what ships when
  no JDK is installed, and it lacked 20 of the 37 widget types (Dialog,
  ListTile, BottomNavigation, Slider, DatePicker, Stepper …), so none of the
  renderer fixes of the last releases reached a device. It is rebuilt from the
  current sources and a test now fails when it falls behind. `Native.java`
  gained `vibrate(long)` and the four-argument `notify(...)` overloads the
  prebuilt `libpymobile.so` still calls.
- **Full screen rebuild on every render** when a screen contained a titled
  Dialog, a BottomNavigation or a Dropdown: these (and ListTile, Stepper,
  ProgressText, SegmentedButtons) are now patched in place.
- ListTile updated only its title; Stepper showed "10 10 10" after an update;
  the Switch branch was unreachable (Switch extends Button) so `checked` never
  synced; Date/TimePicker went blank after an update; `Image.source` and
  `bold=False` were ignored; Slider ignored `step`; ProgressText had no label.
- `TextInput.clear()` (any programmatic change) was dropped while the field had
  focus. Text inputs carry a `revision` prop: programmatic changes are applied
  even with focus, echoes of what the user typed are not.
- Theme colours were hard-coded in Java. The device renderer now receives the
  palette (`theme` in the root of the tree) and dark themes paint dark surfaces.
- `Dialog` is a real modal `android.app.Dialog`; back / tap outside calls
  `Dialog.dismiss()` (`ConfirmDialog` → cancel, `AlertDialog` → acknowledge).
- Changing the system language or font size recreated the Activity and
  restarted Python (all in-memory state lost). `configChanges` now includes
  `locale|layoutDirection|fontScale|density|smallestScreenSize`; the screen is
  redrawn and a language change is published as the `app:locale` event.
- Concurrent permission requests shared one latch; each request has its own.
  Back uses `OnBackInvokedCallback` on Android 13+ (predictive back).

### Fixed — build

- **The debug keystore lived in `build/`**, so `pymobile clean`, `build --clean`
  or a fresh CI checkout created a new key and the next APK could not be
  installed over the previous one. The key is kept in
  `~/.pymobile/keystores/<package>-debug.jks` (`PYMOBILE_KEYSTORE_DIR` to
  override); a key found in `build/` is adopted automatically.
- **`build --native --keystore …` produced a debug-signed APK**: the release
  signing options were never passed to the native backend.
- `optimize = true` was silently ignored for native builds. It is honoured when
  the build runs on Python 3.14 (the device's version) and warns otherwise.
- Desktop-only framework files (compiler, CLI, Tk/web previews, watcher, Java
  sources and prebuilt artefacts) are no longer copied into every APK.
- `pymobile/logging.py` shadowed the standard library for scripts started from
  inside the package. It is `pymobile.log` now; `pymobile.logging` remains an
  alias.

### Fixed — storage

- A corrupt store file was silently treated as empty and overwritten on the
  next write (data loss). It is moved aside to `<name>.corrupt-<timestamp>`
  and a warning is logged.
- `transaction()` is all-or-nothing: changes are written once when the outermost
  block ends; if it raises, memory is rolled back and the file is not touched.
- A non-JSON value is rejected before it reaches memory (it used to break every
  later write of any key); `get()` returns a copy, so in-place mutation can no
  longer diverge from the file.
- Writes `fsync` the file and its directory; a warning is logged when another
  process changed the file since it was loaded.
- `App()` uses the `package` from `pymobile.toml`, so desktop projects no longer
  share one store file.

### Fixed — HTTP

- An exception in `HttpFuture.then(on_success)` was swallowed and `get()` then
  raised it instead of returning the response. It is logged with its traceback
  and passed to `on_error`; `get()` returns the response.
- `HttpFuture.then()` documented that callbacks run "on the calling thread";
  they run on the worker thread, and the docstring now says so.

## [0.7.3] — 2026-09-25

### Security

- **XSS in the browser preview (`web.py`):** widget ids and BottomNavigation tab
  labels were interpolated into inline `onclick`/`oninput`/`onchange` handlers
  by string concatenation, so a crafted `Widget(id=...)` could break out of the
  JavaScript string and execute script in the preview (which `run --web` serves
  on `0.0.0.0`, i.e. the whole LAN). Every handler now reads the id from
  `this.dataset.wid`, and the remaining string values are escaped through a
  dedicated `_js_value()` before being embedded in single-quoted JS strings.

### Fixed

- **Android lifecycle — app was dead after Activity recreation:** `PythonRuntime`
  kept a static `started` flag that stayed `true` after the interpreter
  finalised, so a recreated Activity believed Python was "still running" and
  never restarted it — the relaunched app stayed blank. The flag is cleared in
  a `finally` block, and a new JNI probe `pythonIsInitialized()` (backed by
  `Py_IsInitialized()`, with a graceful fallback for older prebuilt bridges)
  detects a still-alive interpreter across recreation.
- **Android lifecycle — dead event-loop / 100% CPU after stop:** the C queue's
  `q_stopped` flag was never reset, so `next_event()` stopped blocking and
  busy-spun forever (or the loop silently died) after `onDestroy()`. The flag
  is now cleared when a fresh interpreter starts its event loop.
- **Android — root "back" button could not close the app:** `App` only called
  `stop()`, which halted the loop while the window stayed on screen (looked
  like a freeze). A new `finish_app()` path (`AndroidBridge` → `Native` →
  `MainActivity.finishApp()` on the UI thread) actually finishes the Activity;
  preview bridges fall back to the old `stop()`.
- **Android — `RadioGroup` lost its selection:** the selected radio button was
  not restored/synchronised from props on the device, so the choice vanished.
  `updateView` now applies `selected` on `RadioButton` and syncs the checked
  child inside `RadioGroup`.
- **Android — `DataTable` edits never reached the screen:** `add_row`/cell edits
  changed the model but the Java table was only ever built fresh. A new
  `updateDataTable` patches headers and cells in place.
- **Android — `Image` with `http(s)`/`data:` sources failed silently:** the
  Python validator accepts them but the device decoded only local files, and a
  failed `decodeFile` returned `null` without a word. Unsupported sources and
  decode failures are now logged with a warning.
- **`EventBus` thread-safety:** `on`/`off`/`off_all`/`emit` previously mutated
  the handler map without a lock, racing when background jobs fired events.
  All operations are now serialised through an internal lock.
- **`JobManager` evicted other jobs:** two jobs under the same `name` share a
  dictionary slot, and the first one completing used to `pop()` that slot —
  dropping the second from accounting. Completion now only clears a slot if it
  still points at the finishing handle.
- **`HttpFuture.cancel()` was misleading:** it suppressed callbacks but the
  request kept running and `get()` still returned the result. Docstrings now
  state exactly that (the network exchange cannot be interrupted portably).
- **`Vibration.pattern()` accepted odd-length arrays:** device and desktop now
  agree — an odd number of values raises `ValueError` with an example
  (`[0, 100, 50, 100]`).
- **`HttpCache` reads raced with `clear()`:** `get()`/`get_stale()` now take the
  same lock as mutations so a concurrent wipe cannot expose a half-cleared
  keyspace (readers were previously lock-free despite the docstring's promise).
- **HTTP client replayed non-idempotent verbs:** `retries` now applies only to
  GET/HEAD/OPTIONS by default; POST/PUT/DELETE are never replayed implicitly
  (a retried POST could perform its side effect twice). Pass `retry_safe=True`
  to opt in.
- **Web preview — images were invisible:** `Image`/`Avatar` are now rendered as
  real `<img>` tags instead of a text placeholder, with an `onerror` fallback
  that swaps in `[image unavailable]`.
- **Web preview — `ScrollView` capped at 60vh:** removed the hard
  `max-height: 60vh` so scroll content uses available space (vertical scrolling
  still works).
- **Dialogs were visible on construction:** `Dialog`/`AlertDialog`/
  `ConfirmDialog`/`BottomSheet` are now hidden by default — a freshly built
  dialog no longer pops up before `open()`; pass `visible=` explicitly to opt
  out.
- **`Scheduler` shadow tick:** a `cancel()` landing between a callback's
  cancellation check and its re-arm used to leave one extra "shadow" tick
  armed. The re-arm now disarms the fresh timer when the handle was cancelled
  in that window.
- **JNI — NULL dereference on OOM:** `queue_push` now checks `strdup` failures
  on the widget-id/type/value strings and drops the event instead of
  dereferencing NULL.
- **JNI — partial stdio redirect + descriptor leak:** `redirect_stdio_to_logcat`
  now reports `pipe()` and `dup2()` failures separately and no longer leaves a
  half-redirected stdio with a stale pipe.
- **JNI — `py_vibrate_pattern` ignored conversion errors:** `PyLong_AsLong`
  results are now checked and return with the Python exception set instead of
  silently garbling the pattern.
- **Packaging:** `.bak` files are excluded from the wheel and from git, and the
  nine stale `*.bak` files (old copies of `app.py`, `widget.py`, `jni.py`,
  `storage.py`, `cache.py`, `pickers.py`, `components.py`, `screen.py`,
  `pymobile_jni.c`) are removed from the repository.
- **CI:** `quality.yml` no longer uploads the `device-smoke` diagnostics that
  nothing generates; `twine check` now runs with `--strict`.

### Changed

- **Build config — honest ABI handling:** `abi` now accepts only the ABIs the
  packaged runtime actually ships (`arm64-v8a`, `x86_64`). A multi-ABI config
  no longer silently ignores the extra architectures — the pipeline emits a
  warning that native builds package only the first requested ABI and asks to
  build one ABI at a time.

## [0.7.2] — 2026-09-21

### Added

- **Widget aliases for common shorthand:** `Slider(min=, max=)`, `Stepper(min=, max=)`, `ProgressBar(max=)`, `RatingBar(max=)`, `ProgressText(max=)`, `TextInput(maxlength=)` now work as aliases for `minimum`/`maximum`/`max_length`. Previously they were silently ignored via `**kwargs` → `Widget` props.
- `Storage.exists(key)` alias for `contains(key)` — more discoverable, documented with examples.
- `EventBus.off(name, handler=None)` now removes all handlers when handler is None and returns count; new `off_all(name)` method.
- `App.off(event, handler=None)` — removes all or specific handlers, returns count.
- `App.replace(screen)` and `App.reset(screen)` shortcuts for `navigator.replace`/`reset`.

### Fixed

- **Style validation:** `Style(font_size="big")` → `TypeError` with hint `Style(font_size=16)`; `Style(color=255)` → `TypeError` with hint `Style(color='#FF0000')`.
- **Layout:** `Column(None, Label("B"))` → `TypeError` with hint about `visible` instead of `AttributeError`.
- **Permissions:** `permissions.has(None)` → `TypeError` with hint instead of `AttributeError`.
- **Components:** `Label.text = None` and `Button.text = None` now convert None → "" (was already in some paths, now consistent).
- **i18n:** Added `Translations.load_dict()` for in-code translations; `load_dict({"en": "not-a-dict"})` now raises `TypeError` with example instead of `ValueError`.
- **Validation:** `Validator({"code": [{"length": 3}]})` — length now accepts int as exact length; `{"matches": "field"}` now does field-to-field comparison via `_MatchesField`.
- **Jobs:** `then(on_error=...)` without `on_success` now works — previously required on_done.
- **Dropdown/SegmentedButtons:** Empty options message now includes example `e.g. options=['Item 1', 'Item 2']`; `set_value("Z")` now shows `available: [...]`.
- **Slider:** `min=100, max=0` now shows values and hint `did you swap them?`; also supports `min`/`max` aliases, previously silently ignored.
- **RadioGroup/SegmentedButtons:** `select("C")` now shows available options.
- **Vibration:** `vibrate(0)` message improved to `milliseconds must be positive, got 0; pass e.g. vibrate(100)`; amplitude 999 and -5 now raise `ValueError`.
- **Scheduler:** `set_interval(0)` message improved to explain `set_timeout` vs interval; `set_timeout("100", ...)` now raises `TypeError` for non-numeric.
- **Lifecycle critical:** `on_show()` was called BEFORE `build()` — now `screen.root` is built before `on_mount`/`on_show`, with rollback if build fails.
- **Lifecycle critical:** `Dropdown.set_value()` during `build()` fired `on_change` callbacks that accessed not-yet-created widgets — now callbacks are suppressed during `build()` via `in_build_scope()` (widget_scope ContextVar). Affects Dropdown, SegmentedButtons, RadioGroup, Slider, Switch, Checkbox, RatingBar, Stepper, TextInput, SearchBar.
- **Navigation:** Push same object message improved with example `SettingsScreen()`; `App.push(None)` now raises `TypeError` with hint instead of `AttributeError`; `Screen.build()` returning None now raises `PyMobileError` with hint instead of `AttributeError`; `pop()` twice on root now documented as safe returning None.
- **HttpClient:** `timeout=-1` and `retries=-1` now raise `ValueError` with clear message; `retries=1.5` float now raises `TypeError`; `base_url` non-string → `TypeError`; `base_url="   "` whitespace-only → `ValueError`; docstring for `get()` now warns about blocking UI thread.
- **Storage:** `del storage["nonexistent"]` now raises `KeyError` with helpful hint (use `delete()` or check `key in storage`) instead of bare `KeyError`; `set()` and `setdefault()` with non-JSON value (e.g. set) now raise `PyMobileError`/`ResourceError` with hint about JSON types instead of raw `TypeError`.
- **Events:** `app.on("event", None)` now raises `TypeError: handler must be callable`; `off()` now supports removing all handlers and returns count.
- **Notifications:** `notify(None, "Body")` now raises `TypeError: title must be a string` instead of `ValueError`; empty title → `ValueError` with example hint; body type also validated; `notification_id` string now raises `TypeError`.
- **Build:** `build --native` without SDK now always hints `pymobile setup-sdk` (ToolchainError already had hint, now cmd_build guarantees it); `build` without `--native` now warns `This is a structural build — not installable on a device. Use --native...`.
- **Permissions:** `request([])` now unpacks list/tuple/set — allows both `request("CAMERA", "LOCATION")` and `request(["CAMERA", "LOCATION"])`.
- **ProgressBar, RatingBar, Stepper, ProgressText, TextInput:** Now support `max`/`min`/`maxlength` aliases.

### Changed

- `Container.add()` now raises `TypeError` for None child with hint about `visible`.
- `EventBus.off()` signature changed from `off(name, handler)` to `off(name, handler=None)` returning int (breaking: previously returned None).
- `Navigator.push()` now validates `isinstance(screen, Screen)` before checking stack.
- `Screen.root` property now validates build() result (None → PyMobileError, non-Widget → PyMobileError).

## [0.7.1] — 2026-09-20

### Fixed

- **`JNIBridge.request_permissions` returned the answer before the user tapped
  anything.** `Activity.requestPermissions()` is asynchronous: the dialog opens
  and the call returns immediately, so a follow-up `checkSelfPermission()`
  always read "not granted" and the rest of the code believed the user had
  refused. The bridge now hands the request to a Java-side callback
  (`onRequestPermissionsResult`) and resolves a `PermissionFuture` from the
  actual answer — the documented "ask, then check" pattern works in
  production, not only against the `StubBridge`.
- **`Image` did not redraw when `source` or `fit` was reassigned.** The fields
  were plain attributes with no `setter`, so assigning `img.source = "…"`
  after construction left the on-screen view on the previous bitmap. They are
  properties now, and the setter invalidates the widget exactly like every
  other reactive attribute, matching the README.
- **`HttpCache.clear()` walked the whole keyspace under the lock.** A cache
  with tens of thousands of entries locked out `get_cached()` for the entire
  iteration, and iterating `self._storage.keys()` while another thread was
  writing could surface a `RuntimeError: dictionary changed size during
  iteration`. The clear now snapshots the keys once and walks the snapshot.
- **`Storage.setdefault()` raced with a concurrent `set()`.** It took the
  internal `RLock`, looked up the key, released the lock and *then* called
  `set()`, so another thread could insert the same key between the check and
  the write — the documented "atomic against jobs, timers and HTTP callbacks"
  contract was not actually atomic. There is now a single locked
  read-modify-write.
- **`Widget.props()` returned the live `_props` dict.** A widget kept its
  reference; the next `setter` mutated the dict the caller had in hand, so a
  test that compared `widget.props() == old_props` could pass on Monday and
  fail on Tuesday after a single render. The method now returns a shallow
  copy, matching what the renderer and the JSON serialiser actually need.
- **`Screen.on_unmount` ran after the subscriptions were already torn down.**
  A handler that tried to remove itself with `screen.off(...)` raised
  `KeyError`, and a handler that published a final event reached zero
  listeners. Subscriptions are detached *after* `on_unmount` returns now, in
  the documented order.
- **`TimePicker.set_value("24:00")` silently rewrote the field to `00:00`.**
  `datetime.time.fromisoformat("24:00")` is an ISO 8601 end-of-day sentinel and
  Python ≤ 3.13 silently turned it into `00:00:00`, so the wall-clock field
  displayed a valid but wrong time. A pre-check rejects any input that starts
  with `"24:"` before parsing (and the post-parse `hour == 24` guard is kept
  as a defence-in-depth fallback for older interpreters). Python 3.14 was
  specifically verified.
- **`Native.dispatchEvent` could dereference a freed `jstring` for a null
  payload.** A bare `"press"` event has no value, so `valueJ` is `NULL` after
  `GetStringUTFChars`. The handler now treats `NULL` as the empty string,
  releases nothing in that branch, and the `jstring` is released in a single
  place — both the previous leak-on-null and the previous
  use-after-free-on-non-null are gone.

## [0.7.0] — 2026-09-20

### Added

- **`BottomNavigation`** — the bottom-tab navigation pattern: a persistent bar
  of equal-width tabs with `value`/`select()`/`on_select`, rendered natively
  (horizontal tab bar), in the browser preview (pinned bottom bar), in the Tk
  window and in the ascii picture.
- **Dialogs and sheets** — `Dialog`, `AlertDialog`, `ConfirmDialog` and
  `BottomSheet`: framed, elevated modal surfaces with `open()`/`close()`
  (`visible`-based, so reactive and free while hidden). Button callbacks fire
  only after the dialog has closed, so handlers never race the overlay.
- **`DatePicker` and `TimePicker`** — ISO-string values with optional
  `minimum`/`maximum` clamping and fail-fast validation; native
  `DatePickerDialog`/`TimePickerDialog` on Android, real `<input type="date">`
  / `<input type="time">` controls in the browser preview.
- **StubBridge call-name reference in the README.** The testing section now
  lists every name recorded in `bridge.calls` (`notify`,
  `cancel_notification`, `vibrate`, `vibrate_pattern`, `cancel_vibration`,
  `toast`, `request_permissions`, `render`), including the gotcha that
  `vibration.preset(...)` records `vibrate_pattern`, not `vibrate`.
- **The README now states which widget attributes are reactive.** `text`,
  `value`, `checked`, `visible` and `enabled` schedule a redraw; `style` is a
  plain attribute, and the documented way to restyle a live widget is a new
  `Style` plus `invalidate()` (or `refresh()` when the tree changed).

### Fixed

- **`run --web` told users to open `http://0.0.0.0:8765`.** `0.0.0.0` is a bind
  address, not a destination: Windows browsers reject it with
  `ERR_ADDRESS_INVALID`, so the banner looked like a broken server. The CLI
  and the server log now print a loopback URL (`browser_url()`) and explain
  that the wildcard bind is what serves containers, SSH tunnels and LAN
  devices.
- **`preview --png` clipped the tail of long lines.** `render_png()` sized the
  canvas from a guessed constant (`scale // 2 + 1` pixels per character) while
  the face itself was loaded at `scale + 2` pixels, whose monospace advance is
  ~0.6 em — so every line past ~45 characters lost its tail (amounts, dates).
  The canvas is now measured with the real glyph advances
  (`font.getlength`), per character and per face.
- **`preview --png` drew tofu boxes for uncovered glyphs without a word.**
  Emoji and symbols missing from the chosen face now fall back, glyph by
  glyph, to a symbol face (Symbola, Segoe UI Symbol, Apple Symbols, Noto Sans
  Symbols 2) when one is installed; whatever remains uncovered is logged once
  with an actionable hint (`PYMOBILE_PREVIEW_FONT`) instead of failing
  silently.
- **A bare argument-rule read "unknown validation rule".**
  `Validator({"name": ["min_length"]})` raised `unknown validation rule:
  'min_length'` although the rule exists — it merely requires an argument.
  The message now says exactly that and shows the one-key mapping spelling
  plus the list of rules that do work bare.

## [0.6.5] — 2026-09-12

### Fixed

- **`get_cached()` sent every query parameter twice.** The URL was built with
  `params` and then `params` was passed to `get()` again, so page 2 arrived as
  `/items?page=2&page=2`. The doubled URL was also the cache key, so a plain
  `get()` with the same params could never reuse what `get_cached()` stored.
- **A dropped connection escaped as a raw `http.client` error.** `_send()`
  caught only `URLError` and `TimeoutError`, so `IncompleteRead`,
  `ConnectionResetError` and `RemoteDisconnected` propagated untouched. Two
  documented behaviours depend on those being `NetworkError`: `retries` never
  retried, and `get_cached()` never fell back to a stale copy, because both
  catch only `NetworkError`. Truncated and malformed replies are now
  `NetworkError` too.
- **Two builds of the same project produced different APKs.** The staging copy
  lives in a fresh `pymobile-build-XXXX` temp directory and its absolute path
  was written into every `.pyc`; the default `TIMESTAMP` invalidation also put
  the copy's mtime in the `.pyc` header, and `shutil.copyfile` stamps the copy
  with the build time. Bytecode is now compiled with `ddir="app"` and
  `UNCHECKED_HASH`, so identical sources give an identical APK byte for byte —
  as the README always claimed.
- **A language or theme change only reached the visible screen.** Screens below
  the top kept the tree they had already built, because `Screen._root` is
  cached and handed back unchanged on `pop()`. Both now rebuild the whole
  stack; only the visible screen re-renders, so the cost is nil.
- **`Screen.refresh()` broke the documented stored-widget pattern.**
  `self.counter = Label("0")` — the pattern the docs recommend, and the reason
  `_name_widgets()` exists — raised `ValueError: widget 'counter' already has a
  parent` on the second `build()`. The discarded tree is now detached first.
- **`Image` rejected Windows paths.** `urlparse(r"C:\photos\me.png")` reports
  the drive letter as a URL scheme, so the most natural form of a local path
  failed with `unsupported image URL scheme: 'c'` before the file was touched.
- **`get_diagnostics()` did not return the documented keys.** The README
  example reads `framework_version` and `log_level`, but the function returned
  `framework` and `level` and never reported the framework version at all. The
  documented keys are present now; the old ones are kept for compatibility.
- **A plugin was activated once per process, not once per app.** `_activated`
  was a set of names that was never reset, so a second `App` in the same
  process — a test, a preview restart — silently got no `activate()` call.
- **Navigating after `app.stop()` blamed `App.run()`.** The error said
  "Navigation happened before App.run()" and told the caller to call `run()`,
  which had already been called. A stopped app now says that it was stopped.
- **`Image` with a missing relative path failed silently.** Absolute paths and
  `file://` URIs raised, but a relative path that exists nowhere — a typo, or a
  packaged asset — produced an empty box and no diagnostic. It is logged once
  per source now.

### Changed

- **`Container.add()` raises `PyMobileError` instead of `ValueError`** when a
  widget already has a parent, so the error can carry an actionable `hint`.
  `PyMobileError` is not a `ValueError`: code that wrapped widget construction
  in `except ValueError` needs `except PyMobileError`.

### Removed

- **`GUIDE.md` and `examples/` left the repository.** The guide had drifted
  behind the API — nothing added after 0.5 (`get_diagnostics`, `HttpCache`,
  `JobHandle`, plugins, `set_theme`) was ever documented there, while the
  README covers all of it. The `device-smoke` app was the fixture for the
  `android-emulator-smoke` CI job, and that job goes with it.

### Repository

- **`MANIFEST.in` is tracked again.** It was listed in `.gitignore` even though
  it ships inside the sdist, so a fresh clone built a different source
  distribution than the published one — without `CHANGELOG.md` and without the
  test suite.

## [0.6.4] — 2026-09-05

### Fixed

- **The package could not be imported on Python 3.10 at all.** `core/config.py`
  imported `tomllib`, which is standard only from 3.11, while the metadata
  advertised `requires-python = ">=3.10"` and a 3.10 classifier. The import now
  falls back to the `tomli` backport, which is declared as a conditional
  dependency for 3.10 only.
- **`.gitignore` swallowed the prebuilt JNI bridge.** A blanket `*.so` rule kept
  `resources/android/prebuilt/arm64-v8a/libpymobile.so` out of git, so a fresh
  clone could not run `pymobile build --native`. The path is now excluded from
  the rule.
- **`pytest` failed at collection on a fresh clone.** `test_release_audit.py`
  imported `tools.release_audit`, and `tools/` is gitignored; the import is now
  skipped when unavailable instead of breaking the whole run.

- **The build mode is part of the cache key.** `pymobile build` followed by
  `pymobile build --native` reported "up to date" and handed back the
  structural package — a 52 KB artifact that cannot be installed on a device,
  presented as a finished APK. The two modes are now cached separately.
- **`storage_path` accepts a directory.** The environment override
  `PYMOBILE_STORAGE_DIR` is a directory, so `App(storage_path=...)` was read as
  one too and failed with a bare `IsADirectoryError` from inside `save()`.
  Directories now get the default filename appended, and the remaining disk
  errors are raised as `ResourceError` with a hint.
- **Desktop stores are per application.** Every project shared
  `~/.pymobile/pymobile_store.json` and overwrote its neighbours' settings; the
  file is now named after the application id. An existing shared store is
  adopted once so no data appears to vanish.
- **`preview --png` renders non-Latin text.** Pillow's ASCII-only default font
  turned Cyrillic and Greek interfaces into rows of boxes; a Unicode TrueType
  face is located on the system instead, overridable with
  `PYMOBILE_PREVIEW_FONT`.
- **One broken widget no longer takes down a desktop render.** Failing widgets
  are replaced by red error text everywhere, as the device renderer already
  did, and exceptions raised by widget callbacks are logged rather than
  propagated out of the event loop.
- **Malformed values from a front end are ignored, not fatal.** A `Stepper`
  receiving `"abc"` logs a warning instead of raising `ValueError` out of
  `handle_ui_event`.
- **Builds no longer embed the wall clock.** Entries added to the native APK
  kept their source mtime, so two identical builds differed; every entry now
  uses the fixed timestamp already used by the structural packager.
- `Dropdown` and `SegmentedButtons` raise `ValueError` when constructed with a
  `value` that is not among the options, instead of silently selecting the
  first one — matching `set_value()` and the rest of the fail-fast API.
- The `pymobile init` template imports `Align` and `Color` from `pymobile`
  rather than the internal `pymobile.core.ui`.

### Added

- `Storage.transaction()`, `Storage.update()`, `Storage.increment()` and
  `Storage.setdefault()` for read-modify-write sequences that must not race
  with jobs, timers or HTTP callbacks.
- `ListTile(on_long_press=...)` — a second per-row action, wired through the
  Android renderer (with haptic feedback), the Tk window and the browser
  preview.

## [0.6.3] — 2026-09-02

### Fixed

- CPython Android runtime archives are now pinned by SHA-256 before extract,
  matching the JDK and SDK command-line tools.
- `notify()` creates the notification channel in Java, so a default
  `pymobile build --native` is enough. `PYMOBILE_BUILD_JNI=1` is no longer
  documented as a requirement for notifications.

### Changed

- Package metadata includes an author email and an Issues URL.

## [0.6.2] — 2026-08-31

### Fixed

- App updates now re-extract Python assets: `PythonRuntime` stamps `.extracted`
  with the installed `versionCode` instead of unpacking only once.
- CI quality gate no longer imports a non-existent `notion.tools.release_audit`
  module. `tools/release_audit.py` and `examples/device-smoke` ship in the repo.
- `JobHandle.then(on_success=...)` works (alias of `on_done`).
- `JobHandle.wait(timeout=)` and `HttpFuture.get(timeout=)` raise `TimeoutError`.
- Cancelling a one-shot job skips `fn` if it has not started.
- `Storage.__getitem__` raises `KeyError` for missing keys (matching `del`).
- `ValidationError` subclasses `PyMobileError`; `PermissionError_` is exported.
- `Avatar("MK")` is initials; `image=` is accepted as an alias of `source`.
- `RadioButton.press()` updates the parent `RadioGroup`.
- Web preview renders the 0.6.0 widgets, dark theme chrome, multiline
  `TextInput` as `<textarea>`, and binds `--host 0.0.0.0` by default.
- `pymobile build --no-optimize` exists; `--optimize` no longer blindly
  overwrites `pymobile.toml`.
- `pymobile build --keystore` / `--ks-pass` / `--key-alias` / `--key-pass`
  sign with a release keystore.
- `setup-sdk` downloads command-line tools on macOS (Intel and Apple Silicon)
  and Temurin JDK 17 for Linux/macOS aarch64.
- `slugify` transliterates Cyrillic so `Скарбничка` is not `app`.
- `doctor` no longer claims "everything looks good" when the Android SDK is
  missing.
- HTTP cache stores bodies as base64; `HttpClient` builds an SSL context with
  certifi; `cache=` accepts a `Storage` for the documented example.
- Default `target_sdk` is 35. Docs name the PyPI package `pymobile-framework`.

### Added

- Trusted Publishing workflow (`.github/workflows/publish.yml`).
- Python 3.14 classifier.

## [0.6.1] 

### Fixed — 2026-08-05

- Prevented deadlocks when a completion callback cancels its own `JobHandle` or
  `HttpFuture`; callbacks now always run after internal locks are released.
- A failed repeating job now completes its handle with the original error.
- `Storage` now rejects non-string keys as its public contract specifies.
- `Image` now accepts validated local `file://` URIs.
- `RatingBar(value=...)` now works as documented.
- `Dropdown` and `SegmentedButtons` accept documented `on_change` as a
  compatibility alias for `on_select`.
- `Validator` now accepts the documented mapping/rule DSL as well as callable
  validators.

### Added

- `App.dispatch(...)` and `UiDispatcher` for explicitly handing background
  results into the next safe UI render.
- Typed serialised UI-tree contract plus a capability registry and parity tests
  for the built-in renderer implementations.
- `HttpSecurityPolicy` with opt-in HTTPS-only and hostname allow-list rules.
- Regression tests for the repaired public contracts.
- A GitHub Actions quality gate and Android API 34 native-APK emulator smoke
  test, with an `examples/device-smoke` fixture.

## [0.6.0] — 2026-08-03

### Added

**UI components**

- `RadioButton` / `RadioGroup` — mutually exclusive selection within a group.
- `SegmentedButtons` — horizontal selection panel (tabs / segmented control).
- `ProgressText` — `ProgressBar` with a text label overlay (e.g. "Downloading 42%").
- `Link` — clickable text that opens a URL in the system browser via the native bridge.
- `DataTable` — simple table with headers and rows.
- `Avatar` — round avatar with initials or an image.
- `Checkbox` — toggle with `on_toggle` callback.
- `Slider` — value slider with `minimum`, `maximum` and `on_change`.
- `RatingBar` — star rating widget.
- `Dropdown` — selection from a list of options.
- `Chip` — compact element for actions, filters or tags.
- `Badge` — small notification counter.
- `Stepper` — increment/decrement numeric value.
- `SearchBar` — text input with search semantics and `on_change`.

**Notifications**

- Fixed notification channel creation: channels are now created reliably on
  all devices. Building with `PYMOBILE_BUILD_JNI=1` ensures the native bridge
  includes the `ensure_channel` entry point.
- Added documentation for the full notification setup: permission declaration,
  runtime request, JNI build flag and device settings check.

## [0.5.1] — 2026-08-02

### Added

**List**

- `List(item_count, builder)` — virtualised list that renders only visible
  rows, so 10 000+ items scroll smoothly.
- `ListTile(title, subtitle, trailing, on_press)` — list row builder.
- Methods: `refresh()`, `scroll_to(index)`. Add/clear are blocked because
  the list is virtualised.

**Plugins**

- `Plugin(name)` base class with `activate(app)`, `on_app_start(app)`,
  `on_app_stop(app)` hooks.
- `PluginRegistry` with `register()`, `activate_all()`, `on_app_start()`,
  `on_app_stop()`.
- Plugins are activated automatically in `App.run()` and stopped in
  `App.stop()`.
- Exported as `Plugin`, `PluginRegistry`, `plugins`.

**Background jobs**

- `JobManager.enqueue(fn)` runs a function once on a background thread;
  `JobManager.every(interval_ms, fn)` repeats until cancelled.
- `JobHandle` with `.then(on_success, on_error)`, `.wait(timeout)`,
  `.cancel()`, `.done`, `.cancelled`, `.result`, `.error`.
- `App.run_job(fn)` and `App.repeat_job(ms, fn)` convenience methods.

**Logger**

- `configure(level, color, log_file=)` — optional file logger with
  timestamps.
- `get_diagnostics()` — returns framework version, platform, Python version,
  log level and active handlers.
- `App(log_file="app.log")` parameter.

**Build**

- NativeBackend accepts `abi=` parameter; hardcoded `arm64-v8a` replaced
  with `self.abi`. Enables `x86_64` builds for emulators and Chromebooks.
- Pipeline passes `config.abis[0]` to the backend.

## [0.5.0] — 2026-08-01

### Added

**Themes**

- `Theme(name, colors)` — semantic colour palette.
- `Theme.light()` and `Theme.dark()` built-in themes.
- `App(theme="light"|"dark"|Theme)` — sets the initial theme.
- `app.set_theme("dark")` — switches at runtime and redraws the visible
  screen (like a language change).
- `app.theme.is_dark`, `app.theme["PRIMARY"]`, `theme.color("TEXT")`.
- Exported as `Theme` from `pymobile` and `pymobile.core.ui`.

**Storage**

- `Storage` — JSON key-value store with atomic writes (temp + rename).
- `App(storage_path=...)` → `app.storage`.
- API: `get`, `set`, `delete`, `contains`, `clear`, `keys`, `items`, `[]`
  operator.
- `default_storage_path()` — Android private files / desktop `~/.pymobile`.
- Override with `PYMOBILE_STORAGE_DIR` environment variable.

**Async HTTP**

- `HttpFuture` — non-blocking HTTP requests.
- `HttpClient.get_async`, `post_async`, `put_async`, `delete_async` — run
  on a daemon thread, UI never blocks.
- `future.then(on_success, on_error)` callbacks.
- `future.get(timeout)` — block until result.
- `future.cancel()` — suppress callbacks.
- `future.done`, `future.cancelled`.
- Exported as `HttpFuture` from `pymobile`.

## [0.4.0] — 2026-07-31

### Added

**Input validation**

- `Validator(fields)` — declarative form/input validation.
- `validate()` returns `dict[str, list[str]]` of errors.
- `validate_or_raise()` raises `ValidationError` on first failure.
- Validators: `required`, `optional`, `email`, `length`, `min_length`,
  `max_length`, `integer`, `number`, `between`, `min`, `max`, `matches`,
  `one_of`, `regex`, `boolean`.
- Pure logic, no UI dependency. Exported as `Validator`, `ValidationError`.

**HTTP cache / offline**

- `HttpCache` — disk-backed cache for GET responses, based on `Storage`.
- `HttpClient(cache=...)` parameter.
- `get_cached(url, ttl=300)` — returns cached response if fresh, stores
  new responses, falls back to stale cache on network failure (offline
  mode).
- `Response.from_cache` flag.

**Snapshot testing**

- `snapshot_path(test_file, name)` — resolves golden file path.
- `assert_snapshot(widget_or_tree, __file__, name=...)` — first run writes
  golden file to `snapshots/`; subsequent runs compare and raise
  `AssertionError` with diff on mismatch.
- `update=True` regenerates the golden file.

**App metadata**

- `App(version=, package=)` parameters.
- `app.info` dict with `name`, `version`, `package`, `platform`.

## [0.3.0] — 2026-07-29

Ten pieces of friction reported from building a real Pomodoro app, addressed
end to end.

### Added

**Layout**

- `Grid(columns=N)` — equal-width cells for cards, galleries and menus.
  Column widths are computed across the whole grid, so two stat cards stay
  aligned whatever they contain; `Row(weight=1)` could never guarantee that.
  Supports `spacing`, `row_spacing` and `column_spacing`.
- `Expanded(child, flex=n)` and `Flexible(child, flex=n)` — Flutter-style
  shares of the free space on the main axis, tight and loose respectively.
- `Divider()` — a hairline between sections, horizontal or vertical, with an
  optional `inset` and `thickness`.
- `SafeArea(content)` — pads content by the real window insets, per edge, so
  it clears the notch, status bar and gesture bar on any device.
- `Row`/`Column` accept `cross_align` (`start`, `center`, `end`, `stretch`),
  so children no longer align differently across the axis by accident.
- `Style` gains `min_width`, `max_width`, `min_height`, `max_height` and
  `aspect_ratio`. Contradictory bounds raise instead of clipping silently.

**Reactivity**

- Widgets redraw themselves. `self.counter.text = "5"` schedules a frame; so
  does `set_text()`, and the same applies to `value`, `checked`, `visible` and
  `enabled`. `app.render()` is no longer something you can forget.
- Redraws are coalesced: a UI callback runs inside an implicit batch, and
  `app.batch()` groups updates explicitly, so six changes still draw once.
  Setting an unchanged value draws nothing.
- `App(auto_render=False)` keeps the old manual behaviour; the first missed
  redraw in that mode logs a warning.

**Events**

- `screen.on(event, handler)` subscribes for the lifetime of the screen and is
  cancelled on unmount — including through `replace()` and `reset()`. Pushing
  a screen twice no longer runs its handler twice or keeps the old instance
  alive. `app.on(..., screen=...)` does the same from outside.

**Developer tooling**

- `pymobile watch` re-renders on every save. Change detection is content-based
  rather than mtime-based, because tmpfs and overlayfs report a coarse
  timestamp and quick saves would be missed. Editing an imported helper counts
  too; a syntax error is reported without ending the session.
- `pymobile run --gui` opens an interactive Tkinter window: buttons run their
  callbacks, switches and fields feed events back through the device code
  path, navigation works and a back button appears when the stack allows it.
  Trees are patched in place, so typing does not lose focus.
- `App.current()` returns the running application.

**Internationalisation**

- `pymobile.core.i18n`: JSON catalogues per language, `{}` interpolation,
  region → language → default fallback, and plural forms with
  `zero`/`one`/`few`/`many`/`other` — Ukrainian's 1 / 2-4 / 5+ pattern works,
  not just English. Missing keys render as the key and are logged once.
- `device_language()` reads the real system locale on Android, honouring
  Android 13 per-app language overrides, and environment variables elsewhere.
- `translations.install_gettext()` for projects with an existing `.mo`
  workflow. Exported as `t`, `translations`, `Translations`.

- `pymobile run --web` serves the interactive preview over HTTP instead of Tk,
  for remote machines, containers and phones on the same network. The page is
  built from the same serialised tree, and widget ids appear as `data-wid`
  attributes for the browser inspector.
- `translations.subscribe()` notifies listeners when the language changes;
  `App` uses it to rebuild the visible screen, so `translations.use("uk")`
  updates the UI immediately.

**Build**

- `pymobile build --minimal-stdlib` drops desktop-only stdlib packages
  (~1.7 MB) and `--no-ssl` drops OpenSSL and the CA bundle (~4.7 MB).
- `--no-ssl` now warns at build time when the sources use `HttpClient`, rather
  than shipping an APK that fails with "No module named *ssl*" on device.

### Fixed

- **APK size: 21.6 MB → 16.6 MB with no flags.** The official CPython Android
  runtime ships each support library twice — `libcrypto.so` and
  `libcrypto_python.so` are byte-identical, as are the ssl and sqlite3 pairs —
  and only the `_python` names appear in the extension modules' `DT_NEEDED`
  entries. The duplicates are no longer packaged. With both new flags the same
  app is 11.7 MB.
- The `config-3.14` stdlib exclude never matched the real directory name
  (`config-3.14-aarch64-linux-android`), so 262 KB of build headers shipped in
  every APK. Excludes now match by prefix.
- `set_interval()` no longer drifts: ticks are scheduled against a fixed
  timeline instead of pausing between runs, so a callback that takes 15 ms no
  longer pushes a 30 ms timer out to 45 ms. Deadlines missed while the device
  slept are skipped rather than fired as a burst.
  `drift_correction=False` restores the old behaviour.
- Widget ids no longer shift when an unrelated widget is added. A widget
  assigned to `self` in `build()` takes the attribute name (`self.counter` →
  `"counter"`); anonymous widgets are numbered per screen and per type.
- Plural lookup preferred `one` over `other` for counts above 1 when both were
  present.
- `Style(margin=...)` was silently ignored on device. `applyStyle()` runs while
  the view is still detached, so `getLayoutParams()` returned `null` and the
  margins were dropped; they are now written where the layout params are
  created. A widget's margin **adds** to its container's `spacing`, which is
  how to give one neighbour a different gap from the rest.
- `Style(width=...)`, `Style(height=...)` and `Style(elevation=...)` were
  documented but never implemented in the renderer. Sizes accept a number in
  dp or the names `"match"`/`"fill"`/`"wrap"`; elevation gets an opaque
  background so the shadow is actually visible.
- `ScrollView` laid its children out itself, ignoring margins and flex shares.
  It now reuses the same sizing rules as `Row`/`Column` and accepts `spacing`.
- A horizontal `ScrollView` was drawn as a vertical stack in both the ASCII
  and the GUI preview.

### Changed

- `Navigator.push`/`replace`/`reset` are generic, so `app.push(Details())`
  keeps its concrete type for type checkers and editors.
- `StubBridge` accepts `language=` and implements `device_language()`.
- The prebuilt `classes.dex` is rebuilt for the new widgets and the
  `deviceLanguage` entry point.

## [0.2.0] — 2026-07-29

### Added

**Timers**

- `app.set_interval(ms, callback)` and `app.set_timeout(ms, callback)` schedule
  work on background threads on every platform, so a clock or poller no longer
  needs `threading` boilerplate. Each returns a `TimerHandle` with `.cancel()`
  and `.cancelled`; all timers are cancelled automatically by `app.stop()`.
- New `pymobile.core.scheduler` module (`Scheduler`, `TimerHandle`), exported
  from the package root.

**Desktop preview**

- `pymobile preview` renders the first screen into a picture on the laptop —
  no emulator. `--png PATH` writes a raster image (needs Pillow), `--ids`
  annotates widgets with their id.
- `pymobile.core.ui.preview.render_ascii()` draws a real 2D layout in text
  (`Row` children sit side by side, `ProgressBar` and `Switch` show their
  state); `render_png()` produces an image. Both accept a live widget or a
  `to_dict` node.
- `StubBridge.last_tree` exposes the most recently rendered tree, which the
  preview command and tests read.

**Ergonomics**

- `Button.set_text()`, mirroring `Label.set_text()`.

### Fixed

- The missing `INTERNET` build warning no longer fires for apps that never use
  the network; the validator now scans the sources for `HttpClient` /
  `app.http` usage first.
- `pymobile.__version__` is now guarded by a test against `pyproject.toml`, so
  the package version cannot silently drift from the distribution version.
- Tests that read source files resolve paths via `__file__` instead of the
  working directory, so the suite passes from any directory (previously 3–9
  failures outside the repository root).

### Changed

- Tests that need the git-ignored `libpymobile.so` prebuilt bridge are now
  skipped, not failed, in a clean source checkout — `pytest` on a fresh clone
  reports green.
- Test count: 316 → 336.

## [0.1.1] — 2026-07-29

### Fixed

- Corrected the project's PyPI badges in the README.

## [0.1.0] — 2026-07-28

First public release.

### Added

**APK build**

- Native backend: `aapt2` → `d8` → `zipalign` → `apksigner`, producing a signed
  (v2+v3) APK that installs on a device.
- Embedded CPython 3.14 for arm64, using the official python.org builds.
- `pymobile setup-sdk` downloads JDK 17 and the Android SDK (~800 MB). The NDK
  is not required: a prebuilt JNI bridge ships with the package.
- Incremental cache — an unchanged rebuild finishes instantly.
- Reproducible output: identical inputs produce a byte-identical APK.

**UI**

- Native renderer: widgets become real Android views.
- Components: `Label`, `Button`, `TextInput`, `Image`, `Switch`, `ProgressBar`,
  `Spacer`.
- Containers: `Column`, `Row`, `ScrollView`, `Stack`.
- Styling: colours, fonts, padding, corner radius.
- Screens with lifecycle hooks and stack navigation; hardware back button.
- In-place view updates that preserve scroll position and keyboard focus.

**Android APIs**

- Local notifications with channels.
- Vibration: one-shot, patterns and six presets.
- Runtime permissions that wait for the user's answer.

**Networking**

- HTTP client on the standard library: GET/POST/PUT/DELETE, JSON, timeouts and
  retries with exponential backoff.
- A root certificate bundle is packaged into the APK, so HTTPS works
  immediately.

**Tooling**

- CLI: `init`, `build`, `run`, `info`, `clean`, `doctor`, `setup-sdk`.
- `python -m pymobile` as a fallback when the scripts directory is not on
  `PATH`.
- Build-time warning for permissions missing from the manifest.
- `StubBridge` for testing applications without an emulator.

**Packaging**

- Modern `pyproject.toml`, typed package (`py.typed`).
- 316 tests; `ruff` and `mypy --strict` report no issues.

### Known limitations

- Only `arm64-v8a` is supported.
- APK size is around 21 MB (interpreter and standard library).
- Minimum supported release is Android 5.0 (API 21).
