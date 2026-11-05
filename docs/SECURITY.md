# Security Policy

PyMobile builds Android APKs from Python source. A build may download SDK tools, a Python runtime, and declared app dependencies; native startup validation may execute the app's **trusted** entry point on the build machine. Treat project code and dependency sources as trusted inputs. Never put signing keys or secrets in the APK, and never commit keystores, signing passwords or other credentials to the repository.

## Supported versions

PyMobile is alpha software.

| Version | Security fixes |
| --- | --- |
| Latest published release on `main` | Aimed here when available |
| Older releases | No guaranteed backports |

See the [changelog](../CHANGELOG.md) for the current published version and unreleased work. This policy is not a guarantee of a fix or a response deadline.

## Reporting a vulnerability

**Please do not open a public issue for an unpatched vulnerability.** Use GitHub's **Report a vulnerability** option on the repository's [Security tab](https://github.com/Maksum867/PyMobile/security) if it is available. Otherwise, email **m02956682@gmail.com**, the maintainer contact published in `pyproject.toml`, with a subject such as `PyMobile security report`. Do not include credentials, signing keys, or personal data in a report.

Where possible, include:

- the affected PyMobile version or commit, platform, and build mode (structural or native);
- a concise description of the impact and the conditions needed to reproduce it;
- minimal reproduction steps or a small example using dummy data;
- any workaround you have found.

The maintainer will try to acknowledge the report, investigate it privately, and coordinate an appropriate disclosure after a fix or mitigation is available. There is no guaranteed response or patch timeline. If private reporting is not possible, avoid publishing exploit details until a safer contact method is available. With the reporter's consent, credit can be given in the advisory or changelog.

## Scope and limitations

Relevant issues include unsafe archive extraction, malicious path handling, credential disclosure, unsafe build/packaging behavior, and vulnerabilities in PyMobile's own runtime bridge or tooling. Third-party Python packages and the Android SDK are maintained separately; report vulnerabilities in those components to their maintainers as well, and tell us if PyMobile's packaging makes the impact worse.

Building a project is a trusted operation by design: the app's own code and its declared dependencies run on the build machine. "PyMobile executes the code I asked it to build" is therefore not a vulnerability by itself; a useful report shows a boundary violation, such as execution driven by an untrusted archive entry, path traversal outside the build directory, or credential disclosure.

A successful desktop startup check is **not** a sandbox or a security audit: trusted application startup code can perform explicit file writes or network requests. PyMobile has not been independently audited. Please do not test against other people's devices, accounts, or services without permission.

## What makes a useful report

A report is welcome even if some information is missing. If safe to collect,
include:

1. The component and entry point involved, affected versions or commit, host OS,
   Python version and, where relevant, Android version, ABI and build mode.
2. The security boundary crossed, prerequisites, realistic impact and whether
   the problem is reproducible with default settings or requires specific options.
3. A minimal, self-contained example and observed versus expected behaviour.
   Use synthetic accounts, files, paths and credentials; redact access tokens,
   cookies, signed URLs, local usernames and other identifying information.
4. Relevant sanitized error output, stack trace or artifact listing and any
   known workaround. Do not send an exploit against a third party or a copy of
   private app data.
5. A safe way to contact you, and whether and how you would like to be credited.

Keep follow-up details in the same private channel where possible. The maintainer
may need clarification or a smaller reproduction. Please avoid public disclosure
of exploit details while a fix and coordinated disclosure are being considered.
Do not assume encryption or a response SLA from an ordinary email address; ask
for a safer channel before sending sensitive material.

## Safe research and disclosure

- Test only systems, accounts, devices and data that you own or are explicitly
  authorized to use. Avoid disruption, data destruction, excessive requests and
  accessing information unrelated to the demonstration.
- Do not use social engineering, expose other users' data or publish credentials
  to demonstrate an issue. Stop and report privately if you encounter real data.
- Acknowledgement, a fix, coordinated disclosure and credit are goals rather
  than guarantees. No reward, bounty or legal safe-harbor commitment is implied.
- If a credential is exposed, revoke or rotate it through its provider promptly;
  deleting a file or commit does not reliably remove copies from history, logs,
  artifacts or caches. Notify the affected owner through a private channel.

## Build and dependency safety

- Review app source, build configuration, dependency names and sources before
  building. A dependency or project can execute code; neither a build nor the
  desktop startup check is a sandbox. Build untrusted material only in a
  separately secured environment with limited access to networks, data and keys.
- Use trusted SDK, runtime and package sources. Review dependency updates and
  transitive dependencies, and pin or otherwise control versions as appropriate
  for your deployment. `pymobile deps check` checks compatibility with available
  wheels; it is not a vulnerability scanner or a guarantee of package safety.
- The default project excludes include common secret-like names, but file-name
  patterns cannot identify every secret. Review app assets, configuration,
  generated APK contents and build logs before distribution. Never rely solely
  on exclusions to protect a credential. Changing exclusions can weaken defaults.
- Review APKs and distributions before publishing, including bundled assets and
  dependencies. A provenance or hash consistency check can detect mismatched
  files; it does not establish the authenticity of the producer or inputs.
- Back up release signing keys securely, restrict access, and plan key recovery.
  Debug keys are for development, not release identity. For release signing,
  prefer `PYMOBILE_KS_PASS` and `PYMOBILE_KEY_PASS` over command-line password
  arguments, which may appear in process listings or shell history. Environment
  variables also require a trusted environment; avoid printing them in logs.
  See [Signing](../README.md#signing).

## Application and network considerations

- App authors are responsible for the data they collect, store and transmit.
  Minimize data and permissions, use dummy data for testing, protect access to
  app files, and do not treat a client-side secret as confidential after shipping.
  `allow_backup` defaults to `false`; changing it affects backup exposure and
  must be reviewed for the app's data and Android environment.
- `HttpSecurityPolicy` is optional and its defaults permit HTTP and any host.
  For production, consider `require_https=True` and an explicit `allowed_hosts`
  set. The policy validates redirect destinations as well as the initial URL;
  see [HTTP requests](../README.md#http-requests). HTTPS and an allowlist are
  useful controls, not a substitute for server-side authorization or safe
  handling of sensitive data.
- Avoid `--no-ssl` for apps that need HTTPS: it removes the SSL libraries and CA
  bundle from the APK. Request only the permissions your app actually needs.

## Browser preview exposure

`pymobile run --web` binds to loopback by default. Binding to another interface
makes the preview reachable over the network. For non-loopback hosts PyMobile
requires a session token by default; treat the printed URL as a credential. The
preview can drive application actions, so use test data and do not share the URL
or logs containing it. HTTP alone does not protect the token in transit; prefer
an authenticated secure tunnel or a properly secured reverse proxy.

The preview rejects foreign `Origin` values by default. `--allow-origin`,
`--embed` and `--no-token` each relax a protection; `--allow-origin '*'` accepts
all origins, and `--no-token` lets anyone who can reach the port drive the app.
Use exceptions only with an access-controlled gateway and a clear need. Stop the
preview when it is no longer needed. See [Desktop preview](../README.md#desktop-preview).
