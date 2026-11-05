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
   PYMOBILE_NATIVE_TESTS=1 python -m pytest pymobile/tests/test_remaining_p_fixes.py
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

## Maintainer continuity (owner actions)

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

## Publication boundary

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
python -m pytest pymobile/tests/test_remaining_p_fixes.py
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
