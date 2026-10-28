"""CLI grammar, independent of command execution and platform services."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping

from . import __version__
from .core.config import CONFIG_FILENAME


def build_parser(
    commands: Mapping[str, Callable[[argparse.Namespace], int]],
    *,
    positive_int: Callable[[str], int],
    default_avd: str,
) -> argparse.ArgumentParser:
    """Construct the argument parser.

    ``--verbose`` and ``--config`` are attached both globally and to every
    sub-command, so ``pymobile -v build`` and ``pymobile build -v`` both work —
    users should not have to remember where a flag belongs.
    """
    # SUPPRESS keeps unset flags out of the namespace, so a sub-command does
    # not silently reset a value given before the sub-command name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help="show detailed output",
    )
    common.add_argument(
        "-c",
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=f"path to {CONFIG_FILENAME} or a project directory",
    )

    # Which screen to show: shared by run, watch and preview.
    where = argparse.ArgumentParser(add_help=False)
    where.add_argument(
        "--screen",
        metavar="NAME",
        help="show this screen (a Screen subclass of the project) instead of the first one",
    )
    where.add_argument(
        "--args",
        dest="screen_args",
        metavar="JSON",
        help=(
            "with --screen: constructor arguments as JSON — an object is keywords "
            "('{\"score\": 7}'), a list is positional ('[7, 10]'). cmd.exe and Windows "
            "PowerShell eat the quotes inside JSON: use --set there"
        ),
    )
    where.add_argument(
        "--set",
        dest="screen_set",
        metavar="NAME=VALUE",
        action="append",
        help=(
            "with --screen: one constructor argument, e.g. --set score=7 --set player=Anna "
            "(repeatable; the value is JSON if it parses — 7, true, [1, 2] — else plain text; "
            "needs no quoting in any shell)"
        ),
    )
    where.add_argument(
        "--navigate",
        metavar="STEPS",
        help=(
            'press your way to a screen: "Menu.start, Quiz.next" takes the step '
            "[Screen.]widget in order; widget=value types a value, <back> goes back, "
            "@Stats picks a bottom-navigation tab"
        ),
    )
    where.add_argument(
        "--tab",
        metavar="NAME",
        help=(
            "pick this bottom-navigation tab after opening the screen, by label or "
            "value (the short spelling of the route step '@NAME')"
        ),
    )

    parser = argparse.ArgumentParser(
        prog="pymobile",
        description="Build Android applications with Python.",
        epilog="Docs: https://github.com/Maksum867/PyMobile",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"pymobile {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    init = sub.add_parser("init", help="create a new project", parents=[common])
    init.add_argument("directory", nargs="?", default=".", help="target directory")
    init.add_argument("-n", "--name", help="application name")
    init.add_argument("-p", "--package", help="package id, e.g. com.example.app")
    init.add_argument("-f", "--force", action="store_true", help="write into a non-empty directory")
    init.set_defaults(func=commands["init"])

    build = sub.add_parser("build", help="compile the project into an APK", parents=[common])
    build.add_argument("-o", "--output", help="output directory")
    build.add_argument("-i", "--icon", help="path to a custom launcher icon")
    build.add_argument(
        "--native",
        action="store_true",
        help=(
            "build a real, signed, installable APK (needs the Android SDK; NDK only to rebuild JNI)"
        ),
    )
    startup = build.add_mutually_exclusive_group()
    startup.add_argument(
        "--check-startup",
        action="store_true",
        default=None,
        help="execute trusted app startup on a desktop stub (default for --native); "
        "10s timeout, not a security sandbox",
    )
    startup.add_argument(
        "--skip-startup-check",
        dest="check_startup",
        action="store_false",
        default=None,
        help="explicitly bypass the native startup check for Android-only startup; "
        "prints an unverified-launch warning",
    )
    build.add_argument("--clean", action="store_true", help="rebuild from scratch")
    build.add_argument("--no-cache", action="store_true", help="ignore the incremental cache")
    build.add_argument("--optimize", action="store_true", help="ship bytecode instead of sources")
    build.add_argument(
        "--no-optimize",
        action="store_true",
        help="ship .py sources even if pymobile.toml has optimize = true",
    )
    build.add_argument("--keystore", metavar="PATH", help="release keystore for --native signing")
    build.add_argument(
        "--ks-pass",
        metavar="PASS",
        help="keystore password; prefer the PYMOBILE_KS_PASS environment variable",
    )
    build.add_argument("--key-alias", metavar="ALIAS", help="key alias inside the keystore")
    build.add_argument(
        "--key-pass",
        metavar="PASS",
        help="key password (defaults to the keystore password; or PYMOBILE_KEY_PASS)",
    )
    build.add_argument(
        "--minimal-stdlib",
        action="store_true",
        help="drop desktop-only stdlib packages (pydoc, unittest, venv, ...): ~1.7 MB",
    )
    build.add_argument(
        "--no-ssl",
        action="store_true",
        help="omit OpenSSL and the CA bundle: ~4 MB smaller, but no HTTPS",
    )
    build.add_argument(
        "--abi",
        choices=("arm64-v8a", "x86_64"),
        help=(
            "CPU architecture of the APK: arm64-v8a (phones, the default) or "
            "x86_64 (the Android Studio emulator); overrides `abis` in pymobile.toml"
        ),
    )
    build.set_defaults(func=commands["build"])

    run = sub.add_parser("run", help="preview the app on this machine", parents=[common, where])
    run.add_argument(
        "--gui",
        action="store_true",
        help="open a clickable window instead of printing to the console",
    )
    run.add_argument(
        "--web",
        action="store_true",
        help="serve a clickable preview in the browser (works over SSH)",
    )
    run.add_argument("--port", type=int, default=8765, help="port for --web (default: 8765)")
    run.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "interface for --web (default: 127.0.0.1, this machine only). Any "
            "other value exposes the preview to the network and requires its "
            "session token"
        ),
    )
    run.add_argument(
        "--token",
        metavar="VALUE",
        help=(
            "with --web: use this session token instead of a generated one — the "
            "value a proxy or an embedding page already knows"
        ),
    )
    run.add_argument(
        "--no-token",
        action="store_true",
        help=(
            "with --web: serve without a session token. Anyone who can reach the "
            "port can drive the app — only behind a proxy that authenticates callers"
        ),
    )
    run.add_argument(
        "--allow-origin",
        metavar="ORIGIN",
        action="append",
        default=[],
        help=(
            "with --web: accept requests whose Origin is this URL (repeatable; "
            "'*' accepts every origin). Needed when a portal embeds the preview "
            "or a proxy rewrites the origin"
        ),
    )
    run.add_argument(
        "--embed",
        action="store_true",
        help=(
            "with --web: omit X-Frame-Options so the page can live in an iframe "
            "(combine with --allow-origin for a portal on another host)"
        ),
    )
    run.set_defaults(func=commands["run"])

    dev = sub.add_parser(
        "dev",
        help="interactive window with live reload (run --gui + watch in one)",
        parents=[common, where],
    )
    dev.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="seconds between source scans (default: 0.5)",
    )
    dev.add_argument("--ids", action="store_true", help="annotate widgets with their id")
    dev.set_defaults(func=commands["dev"], png=None, text=False)

    watch = sub.add_parser(
        "watch",
        help="re-render automatically when a source file changes",
        parents=[common, where],
    )
    watch.add_argument(
        "--png", metavar="PATH", help="write a mockup of the screen as a PNG on every reload"
    )
    watch.add_argument(
        "--text", action="store_true", help="with --png: write the text picture instead"
    )
    watch.add_argument("--ids", action="store_true", help="annotate widgets with their id")
    watch.add_argument(
        "--interval",
        type=float,
        default=0.2,
        metavar="SECONDS",
        help="how often to poll for changes (default: 0.2)",
    )
    watch.set_defaults(func=commands["watch"])

    preview = sub.add_parser(
        "preview",
        help="draw a screen (the first, or --screen / --navigate) as a desktop picture",
        parents=[common, where],
    )
    preview.add_argument(
        "--png",
        metavar="PATH",
        help="save a mockup of the screen as the phone draws it (needs Pillow)",
    )
    preview.add_argument(
        "--text",
        action="store_true",
        help="with --png: save the text picture instead of the mockup (the pre-0.8 output)",
    )
    preview.add_argument(
        "--theme",
        choices=("light", "dark"),
        help="with --png: build the tree and draw it in this theme instead of the app's",
    )
    preview.add_argument(
        "--size",
        metavar="WxH",
        help=(
            "with --png: screen size in dp, e.g. 360x640 "
            "(default: 360 wide, as tall as the content)"
        ),
    )
    preview.add_argument("--ids", action="store_true", help="annotate widgets with their id")
    preview.set_defaults(func=commands["preview"])

    check_ui = sub.add_parser(
        "check-ui",
        help="warn about accessibility gaps and likely narrow-screen overflow",
        parents=[common, where],
    )
    check_ui.add_argument(
        "--width",
        type=positive_int,
        default=320,
        metavar="DP",
        help="narrow screen width to check (default: 320 dp)",
    )
    check_ui.add_argument(
        "--min-touch-target",
        type=positive_int,
        default=48,
        metavar="DP",
        help="minimum explicit control width/height to accept (default: 48 dp)",
    )
    check_ui.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 1 when advisory warnings are found",
    )
    check_ui.add_argument("--json", action="store_true", help="print machine-readable warnings")
    check_ui.add_argument("--theme", choices=["light", "dark"], help="audit using this theme")
    check_ui.set_defaults(func=commands["check_ui"])

    info = sub.add_parser("info", help="show the resolved configuration", parents=[common])
    info.add_argument("--json", action="store_true", help="machine-readable output")
    info.set_defaults(func=commands["info"])

    clean = sub.add_parser("clean", help="remove build artifacts", parents=[common])
    clean.set_defaults(func=commands["clean"])

    widget_group = sub.add_parser("widget", help="create project-local custom widgets")
    widget_actions = widget_group.add_subparsers(dest="widget_action", required=True)
    widget_add = widget_actions.add_parser(
        "add",
        help="generate a Python widget and project-local Android renderer",
        parents=[common],
    )
    widget_add.add_argument("type_name", metavar="TYPE", help="custom type name, e.g. BarChart")
    widget_add.add_argument(
        "-p",
        "--prop",
        action="append",
        default=[],
        metavar="NAME[:TYPE]",
        help="widget prop; TYPE is str (default), int, float, bool or list; repeat as needed",
    )
    widget_add.set_defaults(func=commands["widget_add"])

    widget = sub.add_parser(
        "widget-java",
        help="print the ViewBuilder.java branch for a custom widget type",
        parents=[common],
    )
    widget.add_argument("type_name", metavar="TYPE", help="the widget's type_name, e.g. BarChart")
    widget.add_argument(
        "-p",
        "--prop",
        action="append",
        default=[],
        metavar="NAME[:TYPE]",
        help=(
            "a prop the Python widget sends; TYPE is str (default), int, float, bool or "
            "list. Repeat for several: -p title -p value:int"
        ),
    )
    widget.add_argument("-o", "--out", metavar="FILE", help="write the guide to FILE, not stdout")
    widget.set_defaults(func=commands["widget_java"])

    setup = sub.add_parser(
        "setup-sdk", help="download the Android SDK/NDK for native builds", parents=[common]
    )
    setup.add_argument("--path", help="install directory (default: ~/.andro)")
    setup.add_argument(
        "--with-ndk",
        action="store_true",
        help="also download the NDK (~2 GB), only needed to rebuild the native bridge",
    )
    setup.add_argument(
        "--with-emulator",
        action="store_true",
        help="also download the x86_64 emulator and API 35 image (several extra GB; no NDK)",
    )
    setup.set_defaults(func=commands["setup_sdk"])

    install = sub.add_parser(
        "install",
        help="install a built APK on a connected Android device/emulator",
        parents=[common],
    )
    install.add_argument(
        "apk",
        nargs="?",
        help="APK path (default: this project's configured build output)",
    )
    install.add_argument(
        "--abi",
        choices=("arm64-v8a", "x86_64"),
        help="choose the matching configured APK",
    )
    install.add_argument(
        "--device",
        metavar="SERIAL",
        help="adb device serial (auto-picks if exactly one is connected)",
    )
    install.add_argument(
        "--sdk",
        metavar="PATH",
        help="Android SDK root (otherwise ANDROID_HOME / ~/.andro/sdk)",
    )
    install.set_defaults(func=commands["install"])

    emulator = sub.add_parser(
        "emulator",
        help="manage a local Android emulator (optional multi-GB download)",
    )
    emulator_actions = emulator.add_subparsers(dest="emulator_action", required=True)
    emulator_create = emulator_actions.add_parser(
        "create", help="create the default x86_64 virtual device"
    )
    emulator_create.add_argument(
        "--name",
        default=default_avd,
        help=f"AVD name (default: {default_avd})",
    )
    emulator_create.add_argument("--sdk", metavar="PATH", help="Android SDK root")
    emulator_create.set_defaults(func=commands["emulator"])
    emulator_list = emulator_actions.add_parser(
        "list", help="list existing Android virtual devices"
    )
    emulator_list.add_argument("--sdk", metavar="PATH", help="Android SDK root")
    emulator_list.set_defaults(func=commands["emulator"])
    emulator_start = emulator_actions.add_parser(
        "start", help="start a virtual device; creates it if missing"
    )
    emulator_start.add_argument(
        "--name",
        default=default_avd,
        help=f"AVD name (default: {default_avd})",
    )
    emulator_start.add_argument("--sdk", metavar="PATH", help="Android SDK root")
    emulator_start.add_argument(
        "--no-window",
        action="store_true",
        help="run headless (useful on CI with acceleration)",
    )
    emulator_start.add_argument("--no-audio", action="store_true", help="disable audio output")
    emulator_start.set_defaults(func=commands["emulator"])

    doctor = sub.add_parser("doctor", help="check the environment", parents=[common])
    doctor.set_defaults(func=commands["doctor"])

    return parser
