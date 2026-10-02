"""A tiny plugin/extension registry.

Lets third-party code extend the framework without touching its internals: a
plugin registers an ``activate(app)`` hook that runs when the app starts, plus
optional lifecycle hooks. This keeps additions isolated and testable while
giving apps a way to bundle reusable behaviour.

Example::

    class MyPlugin:
        name = "myplugin"

        def activate(self, app):
            app.on("app:start", ...)

    plugins.register(MyPlugin())
    app = App("Demo")
    plugins.activate_all(app)
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

from ..log import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .app import App

__all__ = ["Plugin", "PluginRegistry", "plugins"]

_log = get_logger("plugins")


class Plugin:
    """    Subclasses set ``name`` and may implement ``activate(app)`` plus optional
    ``on_app_start(app)`` / ``on_app_stop(app)`` hooks. ``activate`` runs **once
    per (plugin, app) pair** — for every application the plugin is activated
    against, including a second app created later in the same process, and
    never twice for the same one.
    """

    #: Unique plugin name.
    name: str = ""

    def activate(self, app: App) -> None:
        """Called when the plugin is activated with the application.

        Subclasses override this to subscribe to events, register widgets, etc.
        """

    def on_app_start(self, app: App) -> None:
        """Optional hook: called when the app starts."""

    def on_app_stop(self, app: App) -> None:
        """Optional hook: called when the app stops."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.name!r}>"


class PluginRegistry:
    """Holds registered plugins and activates them against an :class:`App`."""

    __slots__ = ("_plugins", "_activated")

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        # plugin name -> every app it was activated for. A set, not a single
        # slot: ``activate_all(A); activate_all(B); activate_all(A)`` used to
        # activate A a second time (only the *latest* app was remembered),
        # duplicating subscriptions and side effects. Weak references so a
        # stopped-and-dropped app does not stay alive inside the registry.
        self._activated: dict[str, weakref.WeakSet[App]] = {}

    @property
    def names(self) -> tuple[str, ...]:
        """Names of all registered plugins."""
        return tuple(self._plugins)

    def register(self, plugin: Plugin) -> None:
        """Register ``plugin``; duplicates are ignored."""
        name = plugin.name or type(plugin).__name__
        if name in self._plugins:
            _log.debug("plugin %r already registered; ignoring", name)
            return
        self._plugins[name] = plugin
        _log.debug("registered plugin %r", name)

    def unregister(self, name: str) -> bool:
        """Remove a plugin by name; returns whether it was present."""
        self._activated.pop(name, None)
        return self._plugins.pop(name, None) is not None

    def activate_all(self, app: App) -> None:
        """Run ``activate`` on every plugin that has not been activated for ``app``.

        The registry is process-wide, so the same plugin can meet several
        applications (tests, a preview shell, a reloaded app). Activation is
        once per (plugin, app) pair: an app that was already activated for is
        skipped even after another app has been activated in between.
        """
        for name, plugin in self._plugins.items():
            activated = self._activated.get(name)
            if activated is None:
                activated = weakref.WeakSet()
                self._activated[name] = activated
            if app in activated:
                continue
            try:
                plugin.activate(app)
            except Exception:
                _log.exception("plugin %r failed to activate", name)
                continue
            activated.add(app)

    def on_app_start(self, app: App) -> None:
        """Dispatch the app-start hook to all plugins."""
        for plugin in self._plugins.values():
            try:
                plugin.on_app_start(app)
            except Exception:
                _log.exception("plugin %r on_app_start failed", plugin.name)

    def on_app_stop(self, app: App) -> None:
        """Dispatch the app-stop hook to all plugins."""
        for plugin in self._plugins.values():
            try:
                plugin.on_app_stop(app)
            except Exception:
                _log.exception("plugin %r on_app_stop failed", plugin.name)

    def clear(self) -> None:
        """Drop all registered plugins and activation state."""
        self._plugins.clear()
        self._activated.clear()

    def __contains__(self, name: object) -> bool:
        return name in self._plugins

    def __len__(self) -> int:
        return len(self._plugins)


#: The process-wide plugin registry.
plugins = PluginRegistry()
