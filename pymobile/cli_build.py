"""The build command: compile a project into an APK.

`cli.cmd_build` is a lazy pass-through to this module, so an application (or a
test) can replace the command without changing the parser. Everything the body
shares with the rest of the CLI — configuration loading, console output, the
clean step — is read from `pymobile.cli` at call time.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import cli
from .compiler.pipeline import BuildPipeline as _Pipeline


def cmd_build(args: argparse.Namespace) -> int:
    """Compile the project into an APK."""
    config = cli._load(args)
    if args.icon:
        config.icon = args.icon
    if args.output:
        config.output_dir = args.output
    if getattr(args, "optimize", False):
        config.optimize = True
    if getattr(args, "no_optimize", False):
        config.optimize = False
    if getattr(args, "minimal_stdlib", False):
        config.minimal_stdlib = True
    if getattr(args, "no_ssl", False):
        config.no_ssl = True
    if getattr(args, "abi", None):
        # One APK per ABI: arm64-v8a for phones, x86_64 for the emulator.
        config.abis = [args.abi]
    config.validate()

    if args.clean:
        cli._clean(config)

    if getattr(args, "ks_pass", None) or getattr(args, "key_pass", None):
        cli._out.warn(
            "passwords on the command line end up in shell history and `ps`; "
            "prefer PYMOBILE_KS_PASS / PYMOBILE_KEY_PASS"
        )

    cli._warn_if_output_is_ignored(config)
    native = getattr(args, "native", False)
    if getattr(args, "bundle", False):
        # An app bundle is a container around a compiled APK; asking for one
        # against a structural build only produced a confusing error later.
        native = True
    if getattr(args, "debug", False) and not native:
        cli._out.error("`--debug` only applies to a native APK (`build --native --debug`)")
        return 2
    if native and not getattr(args, "no_check", False) and cli._has_files(config.root):
        from .compiler.precheck import check_project, format_finding

        findings = check_project(config.root, run_app=False)
        if findings:
            for finding in findings:
                print(format_finding(finding, config.root))
            cli._out.error(
                f"{len(findings)} problem(s) found; the build was stopped. "
                "Fix them, or pass --no-check to build anyway."
            )
            return 1
    if native:
        cli._out.info("native build: this may take a few minutes on the first run")
    else:
        cli._out.warn(
            "This is a structural build — not installable on a device. "
            f"Use --native for a real APK (requires Android SDK: {cli._invocation()} setup-sdk)"
        )
    # Read from cli, so a caller (or a test) that replaces cli.BuildPipeline wins.
    build_pipeline: type[_Pipeline] = cli.BuildPipeline
    pipeline = build_pipeline(
        config,
        use_cache=not args.no_cache and not args.clean,
        native=native,
        check_startup=getattr(args, "check_startup", None),
        on_stage=lambda stage: cli._out.info(f"{stage}…") if args.verbose else None,
        keystore=Path(args.keystore) if getattr(args, "keystore", None) else None,
        keystore_password=getattr(args, "ks_pass", None),
        key_alias=getattr(args, "key_alias", None),
        key_password=getattr(args, "key_pass", None),
        debug=getattr(args, "debug", False),
        bundle=getattr(args, "bundle", False),
    )
    try:
        result = pipeline.run()
    except Exception as exc:
        from .compiler.startup import StartupCheckError
        from .compiler.toolchain import ToolchainError
        from .compiler.widgets import MissingRendererError

        if isinstance(exc, ToolchainError):
            hint = getattr(exc, "hint", None) or ""
            if "setup-sdk" not in hint:
                hint = (f"{hint} " if hint else "") + (
                    f"Run `{cli._invocation()} setup-sdk` to install it automatically."
                )
            raise type(exc)(str(exc), hint=hint.strip()) from exc
        if native and not isinstance(exc, (MissingRendererError, StartupCheckError)):
            from .errors import PyMobileError as _PyErr

            if isinstance(exc, _PyErr) and exc.hint and "setup-sdk" not in exc.hint:
                exc.hint = f"{exc.hint} (for native: {cli._invocation()} setup-sdk)"
            elif isinstance(exc, _PyErr) and not exc.hint:
                exc.hint = f"Run `{cli._invocation()} setup-sdk` to install the Android SDK."
        raise

    for warning in result.warnings:
        cli._out.warn(warning)

    if result.cached:
        size = (
            f"{result.size / (1024 * 1024):.1f} MB"
            if result.size >= 1024 * 1024
            else f"{result.size_kb:.1f} KB"
        )
        cli._out.ok(f"up to date: {result.apk.name} ({size})")
        cli._out.hint("use --clean to force a full rebuild")
        return 0

    cli._out.ok(result.summary())
    cli._out.field("apk", result.apk)
    cli._out.field("icon", "default" if result.icon_is_default else config.icon)
    if result.native:
        cli._out.field("install", f"{cli._invocation()} install {result.apk}")
        if config.abis and config.abis[0] == "x86_64":
            cli._out.hint("x86_64 build: for the Android Studio emulator, not for a phone")
    if args.verbose:
        for timing in result.timings:
            cli._out.field(timing.name, f"{timing.seconds * 1000:.0f} ms")
    return 0
