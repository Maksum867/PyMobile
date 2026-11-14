"""AndroidManifest.xml generation.

Built with :mod:`xml.etree` rather than string templates: escaping is handled
for us and the result is guaranteed to be well-formed XML.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.dom import minidom

from ..core.api.permissions import normalize
from ..core.config import ProjectConfig

__all__ = ["build_manifest", "ManifestBuilder"]

ANDROID_NS = "http://schemas.android.com/apk/res/android"

_ORIENTATION_MAP = {
    "portrait": "portrait",
    "landscape": "landscape",
    "sensor": "sensor",
    "user": "user",
}


class ManifestBuilder:
    """Turns a :class:`~pymobile.core.config.ProjectConfig` into a manifest."""

    def __init__(
        self,
        config: ProjectConfig,
        *,
        activity: str = "org.kivy.android.PythonActivity",
        app_name: str | None = None,
        debuggable: bool = False,
    ):
        self.config = config
        self.activity = activity
        self.app_name = app_name if app_name is not None else config.name
        #: ``android:debuggable="true"``. Off unless the build asked for it: a
        #: debuggable APK lets any holder of the (public, well-known) debug
        #: certificate attach a debugger and run ``adb shell`` as the app.
        self.debuggable = debuggable

    def _attr(self, element: ET.Element, name: str, value: str) -> None:
        """Set an ``android:`` namespaced attribute."""
        element.set(f"{{{ANDROID_NS}}}{name}", value)

    def build_tree(self) -> ET.Element:
        """Construct the manifest element tree."""
        config = self.config
        # register_namespace makes ElementTree emit the xmlns:android
        # declaration itself; setting it manually would duplicate the attribute.
        ET.register_namespace("android", ANDROID_NS)
        manifest = ET.Element("manifest")
        manifest.set("package", config.package)
        self._attr(manifest, "versionCode", str(config.version_code))
        self._attr(manifest, "versionName", config.version)

        uses_sdk = ET.SubElement(manifest, "uses-sdk")
        self._attr(uses_sdk, "minSdkVersion", str(config.effective_min_sdk))
        self._attr(uses_sdk, "targetSdkVersion", str(config.target_sdk))

        for permission in sorted({normalize(p) for p in config.permissions}):
            node = ET.SubElement(manifest, "uses-permission")
            self._attr(node, "name", permission)

        application = ET.SubElement(manifest, "application")
        self._attr(application, "label", self.app_name)
        # The adaptive icon file name. Android prefers `@mipmap/ic_launcher`
        # whenever it is present (mipmap-anydpi-v26), and falls back to the
        # legacy `icon.png` squares on Android 7 only through the same name —
        # so the packaged legacy squares are written as `ic_launcher.png` too.
        self._attr(application, "icon", "@mipmap/ic_launcher")
        self._attr(application, "roundIcon", "@mipmap/ic_launcher_round")
        # Backups copy the private store (tokens, personal data) off the
        # device; opt in with `allow_backup = true`.
        self._attr(application, "allowBackup", "true" if config.allow_backup else "false")
        self._attr(application, "hardwareAccelerated", "true")
        # Back gestures arrive through OnBackInvokedCallback (API 33+);
        # onBackPressed() is deprecated and ignored with predictive back.
        self._attr(application, "enableOnBackInvokedCallback", "true")
        # Without this the framework never mirrors the layout for Arabic or
        # Hebrew, however the margins are expressed (they are start/end now).
        self._attr(application, "supportsRtl", "true")
        # `adb shell run-as`, Layout Inspector and the Android Studio debugger
        # all refuse to attach to a non-debuggable app — including one signed
        # with the debug certificate, which is what a plain `build --native`
        # produces. Opt in with `build --native --debug`.
        if self.debuggable:
            self._attr(application, "debuggable", "true")

        # -- certificate pinning (S7) --------------------------------------
        # Android honours <network-security-config> only through a meta-data
        # reference; the file itself is a resource, written by the native
        # backend. Only the domains listed are pinned, everything else keeps
        # the system trust store.
        if config.network_pins:
            meta = ET.SubElement(application, "meta-data")
            self._attr(meta, "name", "android.security.networkSecurityConfig")
            self._attr(meta, "resource", "@xml/network_security_config")

        activity = ET.SubElement(application, "activity")
        self._attr(activity, "name", self.activity)
        self._attr(activity, "label", self.app_name)
        self._attr(activity, "exported", "true")
        self._attr(activity, "launchMode", "singleTask")
        self._attr(activity, "screenOrientation", _ORIENTATION_MAP[config.orientation])
        # Every change listed here is handled by MainActivity instead of the
        # activity being destroyed and recreated — recreation used to restart
        # the embedded Python interpreter and lose all in-memory app state
        # when the user changed the system language or the font size.
        self._attr(
            activity,
            "configChanges",
            "keyboard|keyboardHidden|orientation|screenSize|smallestScreenSize|"
            "screenLayout|uiMode|locale|layoutDirection|fontScale|density",
        )

        intent_filter = ET.SubElement(activity, "intent-filter")
        action = ET.SubElement(intent_filter, "action")
        self._attr(action, "name", "android.intent.action.MAIN")
        category = ET.SubElement(intent_filter, "category")
        self._attr(category, "name", "android.intent.category.LAUNCHER")

        return manifest

    def to_xml(self, *, pretty: bool = True) -> str:
        """Render the manifest as an XML document."""
        raw = ET.tostring(self.build_tree(), encoding="unicode")
        if not pretty:
            return f'<?xml version="1.0" encoding="utf-8"?>\n{raw}'
        parsed = minidom.parseString(raw)
        pretty_xml = parsed.toprettyxml(indent="    ")
        lines = [line for line in pretty_xml.splitlines() if line.strip()]
        return "\n".join(lines) + "\n"


def _xml_escape(text: str) -> str:
    """Escape a value for an XML text node or attribute."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def network_security_config(pins: dict[str, list[str]]) -> str:
    """The ``res/xml/network_security_config.xml`` for a pinned build (S7).

    Each listed domain trusts *only* the given certificates. ``cleartextTraffic``
    stays denied, exactly as it is without a config, so this cannot accidentally
    re-enable plain HTTP.
    """
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<network-security-config>",
        '    <base-config cleartextTrafficPermitted="false" />',
    ]
    for domain in sorted(pins):
        lines.append('    <domain-config cleartextTrafficPermitted="false">')
        lines.append(f'        <domain includeSubdomains="true">{_xml_escape(domain)}</domain>')
        lines.append("        <pin-set>")
        for pin in pins[domain]:
            digest = pin[len("sha256/") :] if pin.startswith("sha256/") else pin
            lines.append(f'            <pin digest="SHA-256">{_xml_escape(digest)}</pin>')
        lines.append("        </pin-set>")
        lines.append("    </domain-config>")
    lines.append("</network-security-config>")
    return "\n".join(lines) + "\n"


def build_manifest(
    config: ProjectConfig,
    *,
    pretty: bool = True,
    activity: str = "org.kivy.android.PythonActivity",
    app_name: str | None = None,
    debuggable: bool = False,
) -> str:
    """Convenience wrapper around :class:`ManifestBuilder`.

    ``activity`` selects the launcher class; the native backend passes its own.
    ``debuggable`` adds ``android:debuggable="true"`` for a ``--debug`` build.
    """
    return ManifestBuilder(
        config, activity=activity, app_name=app_name, debuggable=debuggable
    ).to_xml(pretty=pretty)
