# Security Policy

PyMobile builds Android APKs from Python source. A build may download SDK tools, a Python runtime, and declared app dependencies; native startup validation may execute the app's **trusted** entry point on the build machine. Treat project code and dependency sources as trusted inputs, and never put signing keys or secrets in the APK.

## Supported versions

PyMobile is alpha software. Security fixes, when available, are aimed at the latest published release on the main branch. Older releases are not guaranteed to receive backports. See the [changelog](../CHANGELOG.md) for the current published version and unreleased work. This policy is not a guarantee of a fix or a response deadline.

## Reporting a vulnerability

**Please do not open a public issue for an unpatched vulnerability.** Use GitHub's **Report a vulnerability** option on the repository's Security tab if it is available. Otherwise, email **m02956682@gmail.com**, the maintainer contact published in `pyproject.toml`, with a subject such as `PyMobile security report`. Do not include credentials, signing keys, or personal data in a report.

Where possible, include:

- the affected PyMobile version or commit, platform, and build mode (structural or native);
- a concise description of the impact and the conditions needed to reproduce it;
- minimal reproduction steps or a small example using dummy data;
- any workaround you have found.

The maintainer will try to acknowledge the report, investigate it privately, and coordinate an appropriate disclosure after a fix or mitigation is available. There is no guaranteed response or patch timeline. If private reporting is not possible, avoid publishing exploit details until a safer contact method is available.

## Scope and limitations

Relevant issues include unsafe archive extraction, malicious path handling, credential disclosure, unsafe build/packaging behavior, and vulnerabilities in PyMobile's own runtime bridge or tooling. Third-party Python packages and the Android SDK are maintained separately; report vulnerabilities in those components to their maintainers as well, and tell us if PyMobile's packaging makes the impact worse.

A successful desktop startup check is **not** a sandbox or a security audit: trusted application startup code can perform explicit file writes or network requests. PyMobile has not been independently audited. Please do not test against other people's devices, accounts, or services without permission.
