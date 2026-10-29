# Accessibility

PyMobile aims to make applications usable with assistive technology and across different screen sizes. This page describes the project's current tools, their limits, and how to report a barrier. It is **not** a claim of WCAG conformance or a certification of apps built with PyMobile.

## What is available

- Widgets can accept `accessibility_label` when visible text does not provide a useful name. `FormField` passes its visible label to its contained `TextInput`; non-informative images can be marked `decorative=True`.
- Android receives content descriptions for supported widgets, and the browser preview retains labels as ARIA labels or alternative text where implemented.
- `python -m pymobile check-ui --strict` checks the rendered screen for missing names, explicitly undersized controls, potential narrow-screen overflow and explicit text/background contrast. Use `--theme light` and `--theme dark` to check both themes, or `--json` in automation. See [UI accessibility and layout audit](../README.md#ui-accessibility-and-layout-audit).

These checks are heuristics, not a replacement for using TalkBack, keyboard navigation, font scaling, localization, and real Android devices. In particular, checks cannot prove that every screen or third-party widget is accessible; unspecified native sizes and colors may not be measured correctly.

## Reporting a barrier

Open a [GitHub issue](https://github.com/Maksum867/PyMobile/issues/new) for an accessibility bug or a missing feature. If the issue form itself is not accessible to you, or the report contains information you prefer not to share publicly, email **m02956682@gmail.com** (the maintainer contact already published in `pyproject.toml`). Do not include personal or sensitive data in a public issue.

Helpful details include the PyMobile version, device and Android version (or browser/desktop preview), assistive technology used, the affected screen/widget, what you expected, what happened, and a minimal example or screenshot if you can provide one. Reports without all these details are welcome. There is no guaranteed response or fix timeline.

Security vulnerabilities should instead follow the private-reporting instructions in the [Security Policy](SECURITY.md).