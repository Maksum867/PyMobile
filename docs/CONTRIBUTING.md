# Maintenance and release gates

This document defines local review/release practice. It does **not** claim that
upstream branch protection, a second maintainer or a stable release branch has
already been configured. Those settings and appointments require the owner.

## Compatibility and adoption

- Treat 0.x as alpha. Consumers should pin an exact version and validate upgrades
  against their own app and Android devices before deployment.
- Prefer additive APIs and aliases. Document lifecycle changes and migrations in
  CHANGELOG; do not remove compatibility aliases in an unrelated bug-fix patch.
- Do not represent rapid releases as a production support or security-response
  guarantee. Publish only after the gates below pass; release cadence itself is
  not evidence of readiness.
- A stable support branch and named support window require an explicit owner
  commitment. None is invented here.

## Minimum release gates

1. Update the public [known-issues ledger](KNOWN_ISSUES.md), CHANGELOG and docs.
   Connect confirmed new bugs to public issues when publishing upstream.
2. Run `python -m pytest`, `python -m ruff check pymobile` and
   `python -m mypy pymobile`. Do not hide a failed mandatory test behind an
   allowed-to-fail pre-release interpreter job.
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
   back navigation and a theme change. Save ABI/API level and sanitised logcat.
   If no device/emulator is available, mark this gate **not performed**, not
   passed because compilation or signing succeeded.
6. Build modern distributions (`python -m build`), run
   `python -m twine check --strict dist/*`, install the wheel outside the
   checkout, and smoke-test the installed CLI. Include new Java/Python modules,
   the dex and provenance manifest. Keep signing keys out of artifacts.
7. Review the change set, evidence and rollback path before creating a release.
   The publishing workflow now runs tests/lint/types/provenance checks before
   uploading, but environment approval and branch rules still need owner setup.

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
