# Accessibility

PyMobile aims to make applications usable with assistive technology and across different screen sizes. This page describes the project's current tools, their limits, and how to report a barrier. It is **not** a claim of WCAG conformance or a certification of apps built with PyMobile.

## What is available

- Widgets can accept `accessibility_label` when visible text does not provide a useful name. `FormField` passes its visible label to its contained `TextInput`; non-informative images can be marked `decorative=True`. For example:

  ```python
  FormField("email", "Email")  # the visible label names the input
  TextInput(accessibility_label="Plant name")
  Switch(accessibility_label="Watering reminder")
  Image("assets/leaf.png", accessibility_label="New leaf on a fern")
  Image("assets/flourish.png", decorative=True)
  ```

- Android receives content descriptions for supported widgets, and the browser preview retains labels as ARIA labels or alternative text where implemented.
- `python -m pymobile check-ui --strict` checks the rendered screen for missing names, explicitly undersized controls, potential narrow-screen overflow and explicit text/background contrast. Useful flags: `--width DP` (narrow-screen width, default 320), `--min-touch-target DP` (default 48), `--theme light` / `--theme dark` to check both themes, and `--json` for automation. See [UI accessibility and layout audit](../README.md#ui-accessibility-and-layout-audit).

## What the audit does not check

These checks are heuristics, not a replacement for real-device testing. In particular, the audit does not verify:

- focus order and actual TalkBack behaviour;
- keyboard navigation in the browser preview;
- font scaling, localization and right-to-left layouts;
- native default sizes and colours that the app does not declare;
- third-party or custom widgets it cannot introspect.

Use TalkBack, keyboard navigation and real Android devices to cover these gaps.

## Reporting a barrier

Open a [GitHub issue](https://github.com/Maksum867/PyMobile/issues/new) for an accessibility bug or a missing feature; prefixing the title with `accessibility:` makes reports easier to find. If the issue form itself is not accessible to you, or the report contains information you prefer not to share publicly, email **m02956682@gmail.com** (the maintainer contact already published in `pyproject.toml`). Do not include personal or sensitive data in a public issue.

Helpful details include the PyMobile version, device and Android version (or browser/desktop preview), assistive technology used, the affected screen/widget, what you expected, what happened, and a minimal example or screenshot if you can provide one. Reports without all these details are welcome. There is no guaranteed response or fix timeline.

Security vulnerabilities should instead follow the private-reporting instructions in the [Security Policy](SECURITY.md).

## Design checklist

Consider accessibility throughout design, implementation and review, rather than
only at the end:

- Give every interactive control a clear, contextual name. Describe the action
  or purpose (for example, “Save changes”), rather than an icon's appearance.
  Do not rely on an icon, placeholder or position alone to explain a control.
- Mark an image decorative only if it adds no information. Describe meaningful
  images concisely; do not put a decorative flag and a label on the same image.
- Provide visible instructions and error messages. Say what went wrong and how
  to recover; do not rely on colour alone to distinguish success, errors,
  selected items or required fields.
- Keep controls comfortably tappable and sufficiently separated. Check that
  labels are not clipped and that content can be reached on narrow screens,
  with longer translated text and with larger system font settings.
- Keep reading and navigation order understandable, including when content
  changes, dialogs open or an error is shown. Do not assume a visual grouping
  automatically produces the right screen-reader experience.
- Test light, dark and custom themes; consider contrast of text, controls and
  focus indicators. Avoid requiring precise gestures or a single input method
  when a practical alternative can be provided.

These are design goals, not a statement that every renderer exposes all required
semantics or that an app is compliant. Test the implementation you actually ship.

## Labels and images

The following are independent widget examples; imports are shown so they can be
tried in Python. Use labels that make sense in the application's context:

```python
from pymobile import FormField, Image, Switch, TextInput

name = TextInput(accessibility_label="Plant name")
reminder = Switch(accessibility_label="Watering reminder")
email = FormField("email", "Email")
illustration = Image("assets/leaf.png", accessibility_label="New leaf on a fern")
ornament = Image("assets/flourish.png", decorative=True)
```

`accessibility_label` is optional; a visible name may already be suitable for
some widgets. For icon-only controls, supply an explicit action-oriented label
rather than relying on an automatically derived icon name. `Image` rejects
combining `decorative=True` with an accessibility label. Review labels after
localization and after changing a control's purpose or state. Do not put
passwords or other sensitive values in accessibility labels.

## Automated audit

Run these commands from an application project. The selected screen and its
state matter: an audit of the initial screen does not cover all routes, tabs,
errors, or conditional content.

```sh
python -m pymobile check-ui --width 320
python -m pymobile check-ui --screen Settings --width 320 --theme dark --strict
python -m pymobile check-ui --theme light --json --strict
```

Replace `Settings` with a screen in your own app. Use `--screen`, `--args`,
`--set`, `--navigate`, or `--tab` when needed to reach the state to audit;
see `python -m pymobile check-ui --help` for their syntax. Audit relevant
screens and states in both light and dark themes, as well as any custom theme.
Choose `--width` and `--min-touch-target` values appropriate to the scenario;
these are in dp. The command executes the app entry point on a desktop stub,
so run it only on trusted app code. Review its output before sharing it.

| Code | What to investigate |
| --- | --- |
| `accessibility-label` | An interactive control or meaningful image has no name recognized by the audit. |
| `touch-target` | An explicitly declared control dimension is below the selected minimum. |
| `narrow-overflow` | A declared width or estimated layout may exceed available width. |
| `contrast` | Explicit text and background colours fall below the audit's contrast threshold. |

The audit uses a 4.5:1 threshold for normal text and 3:1 for text it considers
large (at least 18 dp, or bold and at least 14 dp). It uses the nearest declared
ancestor background, with the app theme background as the CLI fallback; it does
not infer an undeclared foreground. Estimates are not actual font or Android
layout measurements. Hidden nodes are skipped. A `ListTile` is treated as
interactive by the audit when it has a press, long-press or swipe handler.

`--json` includes severity, code, widget ID, message and hint. By default the
command reports warnings without failing for them; `--strict` returns a nonzero
status when warnings are found. A clean report means only that these heuristics
found no warnings in the audited tree, not that the whole app is accessible.

For a widget or serialized tree in Python, the same audit is available directly:

```python
from pymobile import Switch, audit_ui

control = Switch(accessibility_label="Watering reminder")
for issue in audit_ui(control, width=320, min_touch_target=48):
    print(issue.code, issue.widget_id, issue.message, issue.hint)
```

## Manual testing guide

Use representative devices and assistive technologies where available. In
addition to the automated audit:

1. Turn on TalkBack on Android. Navigate each important screen in reading order;
   check that control names, roles and state are understandable and actions
   can be reached and activated. Verify changes, dialogs and back navigation.
2. Use keyboard navigation in the browser preview where applicable. Check focus
   visibility, logical order and operation without a mouse. Browser preview
   behaviour does not establish Android behaviour, or vice versa.
3. Increase system text size and use a narrow display. Check scrolling, clipping,
   overlapping controls, readable errors and reachable actions. Repeat with
   long translations and right-to-left content where supported by the app.
4. Check themes and non-colour cues. Exercise empty, loading, error and success
   states, not just the initial screen. Confirm that meaningful images convey
   their purpose and decorative ones do not add distracting announcements.
5. Record the device, Android or browser version, assistive technology and
   settings, screen and steps, expected and actual result. Mark anything not
   tested as such. Do not record private information without consent.

When an assistive technology or device is unavailable, say what could not be
verified; do not infer a pass from the desktop preview or an automated check.
