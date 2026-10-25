"""Playback and recording, without blocking the UI.

Two objects, both usable from an event handler:

    player = AudioPlayer("assets/beep.wav", on_end=lambda: print("done"))
    player.play()                       # returns immediately
    player.volume = 0.4

    recorder = AudioRecorder()
    recorder.start()                    # asks for RECORD_AUDIO if needed
    ...
    take = recorder.stop()              # a PickedFile you can upload or keep

The player is a thin wrapper over Android's ``MediaPlayer`` (through the
:mod:`~pymobile.core.bridge` contract, so it works in tests too): one instance
per file, prepared asynchronously, and polled on the app's own scheduler rather
than with a busy thread. That polling is what makes ``on_tick``/position work
on every Android version, including the ones without ``onPositionChanged``.

``seek``/``duration``/``volume`` are deliberately the four knobs an app
actually reaches for — a full media-session API is not the point here.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...errors import PlatformError
from ...log import get_logger
from ..bridge import Bridge, PickedFile, get_bridge

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..app import App

__all__ = ["AudioPlayer", "AudioRecorder", "DEFAULT_TICK_MS"]

_log = get_logger("api.audio")

#: How often the player polls the platform for a position, in milliseconds.
#: A tick costs one JNI call, so 100 ms keeps ``on_tick`` smooth without
#: keeping the CPU awake; the cadence is per-player so a metronome can ask for
#: something finer and a long podcast for something coarser.
DEFAULT_TICK_MS = 100


class AudioPlayer:
    """Play one audio file (asset, ``file://`` URI or ``https://`` URL)."""

    __slots__ = (
        "_source",
        "_bridge",
        "_app",
        "_on_tick",
        "_on_end",
        "_on_error",
        "_tick_ms",
        "_handle",
        "_handle_timer",
        "_polling",
        "_loop",
        "_volume",
        "_muted_volume",
        "_position",
        "duration",
        "loop",
    )

    def __init__(
        self,
        source: str | Path,
        *,
        on_tick: Callable[[int], None] | None = None,
        on_end: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        loop: bool = False,
        volume: float = 1.0,
        bridge: Bridge | None = None,
        app: App | None = None,
    ) -> None:
        if not isinstance(source, (str, Path)):
            raise TypeError(
                f"source must be a path or URL string, got {type(source).__name__!r}; "
                "pass AudioPlayer('assets/beep.wav')"
            )
        text = str(source)
        if not text:
            raise ValueError("audio source must not be empty")
        if on_tick is not None and not callable(on_tick):
            raise TypeError("on_tick must be callable and take a position in milliseconds")
        for name, callback in (("on_end", on_end), ("on_error", on_error)):
            if callback is not None and not callable(callback):
                raise TypeError(f"{name} must be callable")
        self._source = text
        self._bridge = bridge or get_bridge()
        self._app = app
        self._on_tick = on_tick
        self._on_end = on_end
        self._on_error = on_error
        self._tick_ms = DEFAULT_TICK_MS
        self._handle: Any = None
        self._handle_timer: Any = None
        self._polling = False
        self._loop = bool(loop)
        self._volume = 1.0
        self._muted_volume = 1.0
        self.volume = volume  # validated by the setter
        self._position = 0
        #: Length of the file in milliseconds, filled in once it is prepared.
        self.duration = 0

    # -- identity ----------------------------------------------------------
    @property
    def source(self) -> str:
        """The file this player was created for."""
        return self._source

    # -- transport ---------------------------------------------------------
    def play(self, *, from_ms: int | None = None) -> None:
        """Start (or resume) playback; returns at once, never blocks the UI."""
        handle = self._require_handle()
        if from_ms is not None:
            handle.seek(max(0, int(from_ms)))
        handle.play()
        if self._loop:
            handle.set_looping(True)
        self._start_polling()

    def pause(self) -> None:
        """Pause, keeping the position (``play()`` resumes from it)."""
        self._stop_polling()
        self._require_handle().pause()

    def stop(self) -> None:
        """Stop and rewind to the beginning."""
        self._stop_polling()
        handle = self._require_handle()
        handle.stop()
        self._position = 0

    def seek(self, position_ms: int) -> None:
        """Jump to ``position_ms``; clamped to the file's length when known."""
        if isinstance(position_ms, bool) or not isinstance(position_ms, (int, float)):
            raise TypeError("position_ms must be a number")
        target = max(0, int(position_ms))
        if self.duration:
            target = min(target, self.duration)
        self._require_handle().seek(target)
        self._position = target

    def release(self) -> None:
        """Free the platform player. Playback stops; a released player is dead."""
        self._stop_polling()
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.release()
            except Exception:  # pragma: no cover - defensive
                _log.exception("releasing the audio player failed")

    # -- state -------------------------------------------------------------
    @property
    def position(self) -> int:
        """Last known position in milliseconds (not a blocking query)."""
        handle = self._handle
        if handle is not None:
            with contextlib.suppress(Exception):  # a released handle
                self._position = max(0, int(handle.position()))
        return self._position

    @property
    def playing(self) -> bool:
        """Whether playback is currently running."""
        handle = self._handle
        if handle is None:
            return False
        try:
            return bool(handle.is_playing())
        except Exception:  # pragma: no cover - defensive
            return False

    @property
    def volume(self) -> float:
        """Playback volume, 0.0 … 1.0."""
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("volume must be a number from 0.0 to 1.0")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"volume must be within 0.0 … 1.0, got {value!r}")
        self._volume = float(value)
        if self._handle is not None:
            self._handle.set_volume(self._volume)

    def mute(self) -> None:
        """Silence playback without forgetting the volume."""
        if self._volume > 0.0:
            self._muted_volume = self._volume
        self.volume = 0.0

    def unmute(self) -> None:
        """Restore the volume ``mute()`` remembered."""
        self.volume = self._muted_volume or 1.0

    @property
    def muted(self) -> bool:
        """Whether the player is currently silent."""
        return self._volume == 0.0

    # -- internals ---------------------------------------------------------
    def _require_handle(self) -> Any:
        if self._handle is None:
            create = getattr(self._bridge, "audio_player", None)
            if create is None:
                raise PlatformError(
                    "this platform cannot play audio",
                    hint="audio needs the Android bridge; a desktop preview records the call",
                )
            self._handle = create(
                self._source,
                on_end=self._on_ended,
                on_error=self._on_failed,
                volume=self._volume,
                looping=self._loop,
            )
            try:
                self.duration = max(0, int(self._handle.duration()))
            except Exception:  # pragma: no cover - not prepared yet on a device
                self.duration = 0
        return self._handle

    def _schedule(self) -> Callable[[int, Callable[[], None]], Any] | None:
        app = self._app
        if app is None:
            from ..app import App

            app = App.current()
        if app is None:
            return None
        return app.set_interval

    def _start_polling(self) -> None:
        """Ask the app for a repeating tick that publishes position and calls back."""
        if self._polling or self._on_tick is None:
            return
        schedule = self._schedule()
        if schedule is None:
            return
        self._polling = True
        self._handle_timer = schedule(self._tick_ms, self._tick)

    def _stop_polling(self) -> None:
        self._polling = False
        handle = self._handle_timer
        if handle is not None:
            handle.cancel()
            self._handle_timer = None

    def _tick(self) -> None:
        """Publish the position and report the end of the file once."""
        if not self._polling:
            return
        try:
            position = self.position
            playing = self.playing
        except Exception:  # pragma: no cover - defensive
            self._stop_polling()
            return
        if position or playing:
            callback = self._on_tick
            if callback is not None:
                try:
                    callback(position)
                except Exception:
                    _log.exception("AudioPlayer.on_tick raised")
        if not playing:
            self._stop_polling()
            # ``playing`` also turns False when the user paused: only a position
            # at (or past) the end means the file finished on its own.
            finished = self.duration and position >= max(0, self.duration - 2 * self._tick_ms)
            if finished:
                self._on_ended()

    def _on_ended(self) -> None:
        """Called by the platform when the file reached its end."""
        self._stop_polling()
        callback = self._on_end
        if callback is not None:
            try:
                callback()
            except Exception:
                _log.exception("AudioPlayer.on_end raised")

    def _on_failed(self, message: str) -> None:
        self._stop_polling()
        callback = self._on_error
        if callback is not None:
            try:
                callback(str(message))
            except Exception:
                _log.exception("AudioPlayer.on_error raised")
        else:
            _log.warning("audio playback failed for %s: %s", self._source, message)


class AudioRecorder:
    """Record from the microphone into the app's private storage.

    ``start()`` asks for ``RECORD_AUDIO`` first when it is not granted, because
    a recorder that silently writes an empty file is worse than one that asks.
    ``stop()`` returns a :class:`~pymobile.core.bridge.PickedFile` pointing at
    the finished file, so recording and picking produce the same shape of
    result and the rest of the app needs one code path.
    """

    __slots__ = ("_bridge", "_permissions", "_handle", "_path")

    def __init__(
        self,
        *,
        bridge: Bridge | None = None,
        directory: str | Path | None = None,
    ) -> None:
        self._bridge = bridge or get_bridge()
        self._permissions: Any = None
        self._handle: Any = None
        self._path = Path(directory) if directory is not None else None

    # -- state -------------------------------------------------------------
    @property
    def recording(self) -> bool:
        """Whether a recording is in progress."""
        if self._handle is None:
            return False
        try:
            return bool(self._handle.is_recording())
        except Exception:  # pragma: no cover - defensive
            return False

    @property
    def elapsed_ms(self) -> int:
        """How long the current recording has been running."""
        if self._handle is None:
            return 0
        try:
            return max(0, int(self._handle.elapsed_ms()))
        except Exception:  # pragma: no cover - defensive
            return 0

    # -- transport ---------------------------------------------------------
    def start(self) -> None:
        """Begin recording, asking for the microphone permission if needed."""
        handle = self._require_handle()
        if not self._bridge.has_permission("android.permission.RECORD_AUDIO"):
            if self._permissions is None:
                from .permissions import PermissionManager

                self._permissions = PermissionManager(self._bridge)
            results = self._permissions.request("RECORD_AUDIO")
            if not results.get("android.permission.RECORD_AUDIO", False):
                raise PlatformError(
                    "the microphone permission was denied, so nothing can be recorded",
                    hint=(
                        'add "android.permission.RECORD_AUDIO" to permissions in '
                        "pymobile.toml and grant the dialog (Android never asks twice "
                        "once it is denied permanently)"
                    ),
                )
        handle.start()

    def stop(self) -> PickedFile:
        """Stop recording and return the finished file."""
        handle = self._require_handle()
        path = handle.stop()
        if not path:
            raise PlatformError(
                "the recorder produced no file",
                hint="start() must be called before stop(); a zero-length "
                "recording is usually a denied microphone",
            )
        target = Path(str(path))
        size = -1
        with contextlib.suppress(OSError):  # pragma: no cover - not readable here
            size = target.stat().st_size
        return PickedFile(
            path=str(target),
            name=target.name,
            size=size,
            mime="audio/mp4",
        )

    def cancel(self) -> None:
        """Throw the recording away (the file is deleted)."""
        handle = self._handle
        if handle is None:
            return
        try:
            handle.cancel()
        except Exception:  # pragma: no cover - defensive
            _log.exception("cancelling the recording failed")

    # -- internals ---------------------------------------------------------
    def _require_handle(self) -> Any:
        if self._handle is None:
            create = getattr(self._bridge, "audio_recorder", None)
            if create is None:
                raise PlatformError(
                    "this platform cannot record audio",
                    hint="recording needs the Android bridge (it is a no-op in a preview)",
                )
            self._handle = create(directory=str(self._path) if self._path else "")
        return self._handle
