# Contributing, maintenance and release gates

This document covers everyday contributions and the maintainer's review/release
practice. It does **not** claim that upstream branch protection, a second
maintainer or a stable release branch has already been configured. Those
settings and appointments require the owner.

## Contributor quickstart

1. Install an editable checkout with the development extras (Python 3.10 or
   newer):
   ```sh
   python -m pip install -e ".[dev]"
   ```
2. Run the standard checks from the repository root:
   ```sh
   python -m pytest
   python -m ruff check pymobile
   python -m mypy pymobile
   ```
   The mypy configuration in `pyproject.toml` excludes `pymobile/tests`; CI
   passes an explicit `--exclude 'pymobile/tests'` flag with the same effect,
   so these commands check the same code everywhere.
3. Create a topic branch from `main`, keep changes focused, and open a pull
   request using the [PR template](../.github/pull_request_template.md).
   Describe the change, how it was validated, and any compatibility impact.
4. Participation is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).
   Report vulnerabilities privately via the [Security Policy](SECURITY.md),
   never in a public issue.

Contributors who change renderer Java, packaging or release behaviour should
also read the release gates below.

### Adding a widget to the previews

The browser preview and the Tk window draw a widget in two places, each split
by widget family:

- `pymobile/core/ui/web.py` — a `_html_<family>` helper per family, picked
  through the `_HTML_RENDERERS` table. A helper that draws several kinds keeps
  one `if kind == ...` guard per kind, so the dispatcher can share it.
- `pymobile/core/ui/gui.py` — a `_build_<family>` method per family, picked
  through `_TK_BUILDERS`. It returns `True` when it drew the node and `False`
  when the node belongs to another guard in the same helper.

Both take a small `_RenderContext`/`_BuildContext` instead of a dozen
parameters, so adding a field does not touch every helper.

### Adding a widget: the whole list

A widget type reaches five places. `pymobile/tests/test_renderer_parity_between_python_and_java.py`
fails, with the missing name, when one of them is forgotten — that test is the
CI half that does not need an SDK.

| Step | File | What to add |
| --- | --- | --- |
| 1. The widget | `core/ui/components.py`, `controls.py`, `layout.py` or `patterns.py` | the class, its validated properties and `props()` |
| 2. The capability row | `core/ui/registry.py`, `WIDGET_CAPABILITIES` | `WidgetCapability("Name", web=True, gui=True)` — `android=` stays `False` until step 5 lands |
| 3. The previews | `core/ui/web.py`, `core/ui/gui.py`, `core/ui/extras_preview.py` | one helper plus one `_HTML_RENDERERS` / `_TK_BUILDERS` entry |
| 4. The text and PNG previews | `core/ui/preview.py`, `core/ui/extras_preview.py` | the ASCII form and, if it draws, `extra_mockup` |
| 5. The phone | `resources/android/java/ViewBuilder.java` (and a `View*.java` file) | `case "Name":`, then `python -m pymobile.compiler.rebuild_dex` and `--check`; needs javac, d8 and `android.jar`, not an NDK |
| 6. The evidence | `pymobile/tests/`, `CHANGELOG.md` | a test named for the behaviour, and one line under the release |

Steps 1–4 and 6 need no Android tooling at all; that is a complete, reviewable
pull request on its own, with the capability row declaring `android=False` and
`pymobile run` warning that the phone will draw a placeholder. Step 5 turns the
same change into native parity, and is where the rebuild commands from the
release gates apply.

## Where to start, and where to ask

Three doors, in the order most people use them:

| You have | Go to |
| --- | --- |
| a question ("how do I …") | GitHub **Discussions → Q&A**. Issues are for things that are wrong, discussions are for things that are unclear. |
| a small fix, no Android needed | the [`good first issue`](../../issues?q=is%3Aissue+label%3A%22good+first+issue%22) label. Each one names the file and the test. |
| a bug you can reproduce | the **Bug report** form; it asks for `python -m pymobile doctor` output, which saves a round trip. |

Nothing here needs permission before you start. Comment on an issue you are
taking so two people do not write the same patch, and open the pull request when
it is green rather than when it is perfect — review is faster than a perfect
patch written in the dark.

New to the project *and* to Python? Read
[TUTORIAL.md](TUTORIAL.md) first (fifteen minutes, no SDK) and
[BEGINNER-MISTAKES.md](BEGINNER-MISTAKES.md) second. Every snippet in both is
checked by tests — the imports against the real package and the commands
against the real CLI — so neither can quietly rot.

## First contribution in ten minutes

The first useful change does not need an Android device, an SDK, or an
understanding of the renderers. This is the shortest path from a clean checkout
to a merged patch.

1. **Pick something small.** The issues labelled `good first issue` are written
   for exactly this: a docstring, a clearer error message, one test that pins a
   behaviour down. Comment on the issue so nobody duplicates the fix.
2. **Get the checkout running.**
   ```sh
   git clone https://github.com/Maksum867/PyMobile.git
   cd PyMobile
   python -m venv .venv
   . .venv/bin/activate          # Windows: .venv\Scripts\activate
   python -m pip install -e ".[dev]"
   python -m pytest -q           # the whole suite, about 90 s
   ```
   Tk previews and PNG snapshots skip themselves when there is no display or no
   Pillow; a skipped test is not a passed test.
3. **Find where the change goes.** Most tasks touch one of:
   - `pymobile/core/ui/components.py`, `controls.py`, `layout.py` — a widget and
     the props it serialises;
   - `pymobile/core/ui/patterns.py` — a composed widget (card, form, calendar);
   - `pymobile/core/ui/web.py` and `gui.py` — the browser and Tk previews, one
     `_html_*` / `_build_*` helper per widget family (see below);
   - `pymobile/core/bridge/` — what the framework asks the phone for;
   - `pymobile/cli*.py` — one module per command family (`cli_build.py` build,
     `cli_preview.py` preview/dev/watch, `cli_device.py` adb/emulator/install);
     `cli.py` holds the shared helpers and a replaceable pass-through per
     command;
   - `README.md` and `docs/` — behaviour that has changed.
4. **Write the test first.** `pymobile/tests/` has one file per behaviour, and
   the name says what is checked (`test_http_connections_and_encoding.py`, not
   `test_audit_round_5.py`). Copy the closest file, run it alone
   (`python -m pytest pymobile/tests/your_file.py -q`), then run the full suite.
5. **Open the pull request.** Describe the problem, the change and the exact
   commands you ran. A maintainer reviews for correctness first, style second;
   a review question is not a rejection.

Things that make a patch easy to accept: the smallest diff that fixes the
problem, a test that fails before the fix and passes after it, a CHANGELOG line
for anything a user can notice, and no unrelated reformatting. Things that make
it hard: a whole-file reformat, a new dependency, or a change to
`resources/android/prebuilt/` without the rebuild commands from the release
gates.

### Where a good first task usually lives

| Task | Files | Checks |
| --- | --- | --- |
| An error message that does not say what to do | the raising module, `pymobile/tests/` | `pytest`, `ruff`, `mypy` |
| A docstring on a public function | the module, `pymobile/` | `ruff`, `mypy` |
| A README paragraph that is out of date | `README.md`, `CHANGELOG.md` | read it as a newcomer |
| A web-preview look that is wrong | `core/ui/web.py`, `core/ui/extras_preview.py` | `pymobile preview --web` |
| A Tk preview that is wrong | `core/ui/gui.py` | `pymobile run --gui` (needs a display) |
| A widget the phone draws wrongly | `resources/android/java/View*.java` | **not** a first task: needs the SDK and a device |

## Compatibility and adoption

- Treat 0.x as alpha. Consumers should pin an exact version and validate upgrades
  against their own app and Android devices before deployment. Release history
  lives in the [CHANGELOG](../CHANGELOG.md).
- Prefer additive APIs and aliases. Document lifecycle changes and migrations in
  CHANGELOG; do not remove compatibility aliases in an unrelated bug-fix patch.
- Do not represent rapid releases as a production support or security-response
  guarantee. Publish only after the gates below pass; release cadence itself is
  not evidence of readiness.
- A stable support branch and named support window require an explicit owner
  commitment. None is invented here.

## Minimum release gates

1. Update the CHANGELOG and docs.
   Connect confirmed new bugs to public issues when publishing upstream.
2. Run `python -m pytest`, `python -m ruff check pymobile` and
   `python -m mypy pymobile` (tests are excluded by the shared mypy config, as
   in CI). Do not hide a failed mandatory test behind an allowed-to-fail
   pre-release interpreter job.
3. After changing renderer Java, with an Android SDK/JDK installed run:
   ```sh
   python -m pymobile.compiler.rebuild_dex
   python -m pymobile.compiler.rebuild_dex --check
   ```
   The rebuild requires javac, d8 and android.jar, not an NDK. The provenance
   manifest records SHA-256 for every compiled Java source and the resulting
   dex. Java hashes normalise CRLF to LF; the dex hash is byte-exact. Check mode
   rejects missing, added or otherwise changed sources and a replaced dex.
   It is a consistency check, not a cryptographic authenticity or device test.
4. Run real SDK builds and signature/prebuilt/cache regressions:
   ```sh
   PYMOBILE_NATIVE_TESTS=1 python -m pytest pymobile/tests/test_native_build_gates.py
   ```
   Set ANDROID_HOME/JAVA_HOME as needed. Standard native builds validate startup;
   do not use `--skip-startup-check` merely to make release gates green.
5. For renderer/lifecycle changes, install a representative APK on Android and
   exercise launch, navigation, input persistence, list/swipe/dialog operations,
   back navigation and a theme change. Record the device model, ABI/API level
   and a sanitised logcat excerpt in the PR description or release notes; keep
   raw logs, APKs and keystores out of the repository. If no device/emulator is
   available, mark this gate **not performed**, not passed because compilation
   or signing succeeded.
6. Build modern distributions (`python -m build`), run
   `python -m twine check --strict dist/*`, install the wheel outside the
   checkout, and smoke-test the installed CLI. Include new Java/Python modules,
   the dex and provenance manifest. Keep signing keys out of artifacts.
7. Review the change set, evidence and rollback path before creating a release.
   The [publishing workflow](../.github/workflows/publish.yml) runs
   tests/lint/types/provenance checks before uploading, but environment
   approval and branch rules still need owner setup.

## CI notes

- The [Quality workflow](../.github/workflows/quality.yml) runs a mandatory
  Python matrix plus a Windows venv job. A separate pre-release interpreter job
  is allowed to fail and never substitutes for a mandatory job.
- The pre-release job's `tools/release_audit.py` step is conditional and runs
  only if that file exists in the checkout; it is not a required release gate.
  The Android diagnostics upload is best-effort (`if-no-files-found: warn`) and
  warns instead of failing when no device artifacts were produced.
- Every third-party action is pinned to a commit SHA, with the release it came
  from in a trailing comment (`# v4.1.7`). A tag is a moving target: whoever
  owns the action can repoint it and run new code with this repository's
  token. Dependabot proposes the SHA bumps weekly, one grouped PR, so keeping
  the pins current costs a review rather than a rewrite. Verify a new SHA
  against the upstream repository (`git ls-remote --tags https://github.com/…`)
  before merging — the comment is a note, not proof.

## Maintainer continuity (owner actions — skip on your first patch)

- Nominate and obtain consent from a second reviewer with Android/runtime
  knowledge. Update CODEOWNERS only with real, consenting collaborators; do not
  fabricate a team to make the bus factor appear solved.
- Configure protected default/release branches and require successful quality
  jobs. Require independent approval for release/environment changes once a
  second reviewer exists.
- Keep recovery instructions for PyPI trusted publishing, signing-key custody,
  repository access and release rollback in a secure, access-controlled place.
  Commit procedures and owner roles, never credentials or recovery codes.
- Review open issues and dependency/security updates on an owner-approved
  schedule. State actual support commitments publicly; do not imply an SLA that
  no maintainer has agreed to provide.

## Publication boundary (owner actions — skip on your first patch)

Local patches cannot appoint upstream maintainers, create upstream issues
without access, configure protections or update the live PyPI page. The files
above make the process and evidence reviewable and ready for owner approval;
they do not erase that external dependency or eliminate single-maintainer risk.

## Choosing and describing work

- Search existing issues and pull requests before starting. For a bug, use the
  [bug report form](../.github/ISSUE_TEMPLATE/bug_report.yml); for an idea, use
  the [feature request form](../.github/ISSUE_TEMPLATE/feature_request.yml).
  A question or small documentation correction can also begin with an issue.
- Explain the problem, affected users, expected and actual behaviour, and the
  smallest reproducer. Include the PyMobile and Python versions, operating
  system, relevant Android API/ABI and build mode where applicable. Remove
  credentials and personal data from examples and logs.
- For larger or potentially breaking work, discuss the approach in an issue
  before investing in an implementation. A feature request is not a promise
  that a change will be accepted.
- Send security reports privately, not as public reproduction steps; see the
  [Security Policy](SECURITY.md).

## Preparing a change

- Keep unrelated refactors and formatting out of a focused fix. Explain any
  prerequisite changes and link the relevant issue in the pull request.
- Reproduce a bug before fixing it when possible. Add a regression test that
  distinguishes the incorrect behaviour from the intended behaviour, including
  relevant failure cases and edge conditions. Describe what could not be tested.
- For public APIs, consider existing callers, input validation, error messages,
  type annotations, documentation, and migrations. Follow the
  [deprecation policy](../README.md#versioning-and-deprecation-policy) instead
  of silently removing a public spelling.
- For user-visible changes, update the relevant documentation and changelog.
  Examples should use actual public names and be runnable in the stated context;
  do not document capabilities the implementation or renderer does not have.
- Keep generated files, build output, local environments, credentials, private
  screenshots and unrelated data out of the patch. Review the full diff before
  submitting, including added binary and generated artifacts.
- Contributions and reviews are subject to the repository's [license](../LICENSE)
  and [Code of Conduct](CODE_OF_CONDUCT.md); contribute only material you have
  the right to submit. Cite third-party sources and respect their licenses.

## Tests and diagnostics

Run checks from the repository root. During development, run a focused test first,
then the full suite before requesting review:

```sh
python -m pytest pymobile/tests/test_native_build_gates.py
python -m pytest
python -m ruff check pymobile
python -m mypy pymobile
```

The focused test file above is an example, not a substitute for choosing tests
that exercise the change. Some native tests are opt-in and need additional SDK
setup; a skipped test is not evidence that its behaviour passed. If a tool or
platform is unavailable, report the exact command and limitation rather than
marking the check as successful.

For application-level tests, the installed pytest plugin provides
`pymobile_session`, `pymobile_app`, `pymobile_bridge`, `pymobile_driver`,
`pymobile_snapshot`, and `pymobile_png`. The session fixture stops the app and
resets shared state during teardown. Driver actions test navigation and callback
behaviour against the stub bridge; they do not prove Android renderer behaviour.
See [Testing your app](../README.md#testing-your-app) for usage and limitations.

Text and PNG snapshots are useful for detecting visual regressions, not for
proving accessibility or device parity. Review a changed snapshot before
accepting it with `pytest --pymobile-update-snapshots`; the same flag updates
both snapshot kinds. PNG snapshots need Pillow. A missing golden fails when
`CI` is set. Do not approve snapshots blindly just to make a test pass.

Useful diagnostics include `python -m pymobile doctor`,
`python -m pymobile info --json`, and `python -m pymobile logs` for device
tracebacks. Review their output before sharing: paths, identifiers or application
logs can contain private information. Test UI changes with
[`check-ui`](ACCESSIBILITY.md#automated-audit) and, when relevant, on Android.

### Errors that name the mistake

A user does not read the framework source. Every error the framework raises on
purpose must name the thing the user wrote, and say what to write instead:

- Name the user's own syntax, not a private attribute. `Column(Label("a"),
  "text")` used to fail with `'str' object has no attribute '_parent'`; it now
  says the string is not a child and shows `Column(Label('text'))`. A screen
  whose `__init__` forgot `super().__init__()` says so, with the lines that fix
  it, instead of `'Home' object has no attribute '_root'`.
- Prefer the shape the user typed. A handler written with parentheses
  (`on_press=self.on_tap()`) is a static mistake: `pymobile check` reports it at
  its line and column, because the call runs once while the tree is built and
  the button then silently does nothing.
- Keep a sentence per cause. `None` in a container is usually a conditional
  expression, a bare function is usually a factory missing its parentheses, a
  list inside a list is flattened one level only — say which one it is.
- Do not change the exception type callers catch. New child validation raises a
  plain `TypeError`, and `WidgetTypeError` stays about `find()`/`get()`; an
  existing test's expected wording (`children must be Widget instances`) is kept
  where it was already part of the contract.
- Add the regression test to the file that already covers that behaviour
  (`test_child_type_errors.py` for children, `test_precheck.py` for `check`,
  `test_build_warnings_and_artifact_names.py` for what a build writes and
  warns about) and quote the sentence the user sees, so a later refactor cannot
  quietly undo it.

## Renderer and packaging changes

- Check how the serialized widget tree, event handling, state updates and
  capability registry interact across the affected renderers. A desktop stub or
  preview is useful feedback but is not an Android device test.
- Cover initial rendering and subsequent updates, navigation/back, callbacks,
  disabled and hidden states, and error handling where relevant. For accessibility
  changes, include labels and real assistive-technology testing when possible.
- A Java renderer change needs the rebuilt dex and its provenance manifest to
  agree with the sources; use the rebuild and check commands in the release
  gates. The manifest detects drift, not malicious substitution.
- The APK name carries the build mode: `build --native` writes
  `app-0.1.0.apk`, a structural `build` writes `app-0.1.0-debug.apk` (plus the
  ABI for a non-arm64 target). A non-installable artifact must never occupy the
  name `pymobile install` looks for — that produced a signature error instead of
  the real cause.
- Anything that ships inside the APK is public: credential-looking files are
  filtered in `collector.is_credential_file()` and listed in the build
  warnings, so a new "copy everything" path must call it too. Debug switches go
  into the manifest only for `--debug` (`ManifestBuilder(debuggable=True)`),
  never by default, and must be part of `build_mode()` so the cache cannot
  hand a debug artifact to a release build.
- The window lifecycle is a wire contract: `MainActivity.onDestroy` sends
  `dispatchEvent("", "__lifecycle__", "stop")` and `onCreate` sends
  `"resume"`; `App._lifecycle()` handles both. Do not add a new JNI entry point
  for this — `libpymobile.so` ships prebuilt and rebuilding it needs an NDK, so
  `dispatchEvent` (the one call every released bridge has) carries the event.
  Anything queued behind a stop is dropped, and `App.save_state()` must stay
  cheap: it runs on the UI thread during teardown.
- `--bundle` builds the `.aab` from the same artifacts as the APK and verifies
  it with bundletool. A change to the manifest, the resource pipeline or the
  asset layout must be checked with a real `build --native --bundle` and
  `bundletool validate`, not only with the ZIP-layout unit tests; the bundle
  layout (`base/manifest`, `base/dex`, `base/res`, `base/lib/<abi>`) is
  stricter than an APK's. `toolchain.find_toolchain()` follows
  `sdk_installer.SDK_PLATFORM` — never `target_sdk` — because the manifest
  level and the compiled level must agree.
- The adaptive icon is generated, not committed: `icon.write_adaptive_icon()`
  writes the three layers plus `mipmap-anydpi-v26`, and the manifest addresses
  `@mipmap/ic_launcher`. A renamed resource must be renamed in all three
  places (icon, manifest, the notification fallback in `core/bridge/jni.py`).
- `target_sdk` above the platform installed by `setup-sdk`
  (`sdk_installer.SDK_PLATFORM`, currently `android-35`) cannot be compiled
  against; the build warns about it. Raising `SDK_PLATFORM` means changing
  `MINIMAL_PACKAGES` and verifying a real native build.
- JNI rebuilding is separate from rebuilding the renderer dex and requires the
  NDK. For work that needs it, see the [native build guide](../README.md#building-an-apk).
  Record SDK/JDK/NDK and device details and avoid claiming an unrun native check.
- Packaging changes should consider both source and wheel distributions, app
  data/exclusions, ABI selection, cached versus clean builds, signing and upgrade
  compatibility. Do not put signing material into test fixtures or artifacts.

## Pull request and review checklist

In addition to the repository's [PR template](../.github/pull_request_template.md),
include:

1. **Problem and approach:** what changed, why, and links to related issues.
2. **Compatibility:** affected public APIs, migration or deprecation, and any
   renderer/platform differences.
3. **Validation:** exact commands and results, relevant test cases, platform and
   device details, and checks not performed with reasons. Distinguish automated
   tests from hands-on verification.
4. **Evidence:** sanitised screenshots or logs when useful, with meaningful alt
   text for images. Never attach tokens, keystores, personal data or raw private
   logs.
5. **Risk and recovery:** relevant side effects, known limitations and a rollback
   or mitigation for changes that may affect existing apps or releases.

Reviewers should check correctness, tests, accessibility, documentation,
compatibility and security implications as relevant; ask questions rather than
assuming intent. Authors should respond to review feedback or explain a different
approach. A review, passing CI or an example screenshot alone is not proof that
all devices and use cases work.
