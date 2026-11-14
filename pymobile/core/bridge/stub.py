"""Desktop implementation of :class:`~pymobile.core.bridge.base.Bridge`.

Instead of failing away from a device, the stub *records* every call and logs
it. That gives two things for free: a usable desktop preview mode, and a test
double the whole suite is built on.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...log import get_logger
from .base import Bridge, BridgeCall, NotificationSpec, PickedFile

__all__ = ["StubBridge", "StubAudioPlayer", "StubAudioRecorder"]

_log = get_logger("bridge.stub")


class StubBridge(Bridge):
    """Records platform calls in memory; grants permissions by policy."""

    name = "stub"
    accepts_theme = True
    accepts_chrome = True

    def __init__(
        self,
        *,
        grant_permissions: bool = True,
        verbose: bool = True,
        language: str = "",
    ) -> None:
        self.calls: list[BridgeCall] = []
        self.notifications: dict[int, NotificationSpec] = {}
        self.granted: set[str] = set()
        self.grant_permissions = grant_permissions
        self.verbose = verbose
        self.last_tree: dict[str, Any] | None = None
        #: Language reported by :meth:`device_language`; empty means "ask the
        #: environment", which is what a desktop preview should do.
        self.language = language
        #: Paths :meth:`pick_file`/:meth:`pick_image` hand back — a test sets
        #: them to act as a user who chose those files.
        self.file_picks: list[str] = []
        #: What :meth:`copy_to_clipboard` put on the clipboard.
        self.clipboard = ""
        #: Every :meth:`share` call, in order.
        self.shared: list[dict[str, str]] = []
        #: Secrets written through :meth:`store_secret`; a desktop run keeps
        #: them in memory only, which is the honest answer to "no keystore".
        self.secrets: dict[str, str] = {}
        #: Players and recorders created through this bridge, for assertions.
        self.players: list[StubAudioPlayer] = []
        self.recordings: list[StubAudioRecorder] = []
        #: Where :meth:`audio_recorder` writes; a test sets a tmp directory.
        self.recordings_dir = ""

    # -- helpers -----------------------------------------------------------
    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append(BridgeCall(name, kwargs))
        if self.verbose:
            _log.info("%s(%s)", name, ", ".join(f"{k}={v!r}" for k, v in kwargs.items()))

    def calls_named(self, name: str) -> list[BridgeCall]:
        """Every recorded call with the given name."""
        return [call for call in self.calls if call.name == name]

    def reset(self) -> None:
        """Forget recorded state (handy between tests)."""
        self.calls.clear()
        self.notifications.clear()
        self.granted.clear()
        self.last_tree = None
        self.clipboard = ""
        self.shared.clear()
        self.players.clear()
        self.recordings.clear()

    def is_available(self) -> bool:
        """The stub answers every call, so it is always available."""
        return True

    # -- notifications -----------------------------------------------------
    def ensure_channel(self, channel_id: str, channel_name: str, importance: int) -> None:
        """Record the notification channel request; nothing is created."""
        self._record(
            "ensure_channel",
            channel_id=channel_id,
            channel_name=channel_name,
            importance=importance,
        )

    def notify(self, spec: NotificationSpec) -> None:
        """Store the notification and record the call."""
        self.notifications[spec.notification_id] = spec
        self._record("notify", id=spec.notification_id, title=spec.title, body=spec.body)

    def cancel_notification(self, notification_id: int) -> None:
        """Drop the stored notification and record the call."""
        self.notifications.pop(notification_id, None)
        self._record("cancel_notification", id=notification_id)

    # -- vibration ---------------------------------------------------------
    def vibrate(self, milliseconds: int, amplitude: int) -> None:
        """Record a one-shot vibration; no device is touched."""
        self._record("vibrate", milliseconds=milliseconds, amplitude=amplitude)

    def vibrate_pattern(self, pattern: list[int], repeat: int) -> None:
        """Record a vibration pattern; no device is touched."""
        self._record("vibrate_pattern", pattern=list(pattern), repeat=repeat)

    def cancel_vibration(self) -> None:
        """Record that any vibration should stop."""
        self._record("cancel_vibration")

    # -- permissions -------------------------------------------------------
    def has_permission(self, permission: str) -> bool:
        """Whether the permission was granted to this stub."""
        return permission in self.granted

    def request_permissions(self, permissions: list[str]) -> dict[str, bool]:
        """Grant the permissions this stub was told to grant and report the outcome."""
        self._record("request_permissions", permissions=list(permissions))
        result: dict[str, bool] = {}
        for permission in permissions:
            granted = self.grant_permissions
            if granted:
                self.granted.add(permission)
            result[permission] = granted
        return result

    # -- ui ----------------------------------------------------------------
    def toast(self, message: str, long: bool = False) -> None:
        """Record a toast message."""
        self._record("toast", message=message, long=long)

    def render(self, tree: dict[str, Any]) -> None:
        """Remember the latest tree and record how big it was."""
        self.last_tree = tree
        self._record("render", root=tree.get("type"), children=len(tree.get("children", ())))

    # -- files -------------------------------------------------------------
    def pick_file(self, accept: str = "*/*", *, multiple: bool = False) -> list[PickedFile]:
        """Return the file picks this stub was given, filtered by nothing."""
        self._record("pick_file", accept=accept, multiple=multiple)
        return [PickedFile(path) for path in self.file_picks]

    def pick_image(self) -> list[PickedFile]:
        """Return the file picks this stub was given."""
        self._record("pick_image")
        return [PickedFile(path) for path in self.file_picks]

    # -- clipboard ---------------------------------------------------------
    def copy_to_clipboard(self, text: str) -> bool:
        """Keep the text where :meth:`clipboard_text` can read it back."""
        self.clipboard = text
        self._record("copy_to_clipboard", text=text)
        return True

    def clipboard_text(self) -> str:
        """The text last copied, or an empty string."""
        return self.clipboard

    # -- share sheet -------------------------------------------------------
    def share(self, text: str = "", url: str = "", subject: str = "") -> bool:
        """Record what was shared and report success."""
        self.shared.append({"text": text, "url": url, "subject": subject})
        self._record("share", text=text, url=url, subject=subject)
        return True

    # -- audio ------------------------------------------------------------
    def audio_player(self, source: str, **options: Any) -> Any:
        """A fake player that advances by wall-clock time (nothing is heard)."""
        player = StubAudioPlayer(source, **options)
        self.players.append(player)
        self._record("audio_player", source=source, **options)
        return player

    def audio_recorder(self, *, directory: str = "") -> Any:
        """A recorder that writes a small file instead of recording sound."""
        recorder = StubAudioRecorder(Path(self.recordings_dir or directory or "."))
        self.recordings.append(recorder)
        self._record("audio_recorder", directory=str(recorder.directory))
        return recorder

    # -- locale ------------------------------------------------------------
    # -- secrets -----------------------------------------------------------
    def store_secret(self, name: str, value: str) -> bool:
        # The value is deliberately not recorded: a call log that quotes a
        # secret is exactly what this API exists to avoid.
        """Keep the secret in memory and record the call without the value."""
        self._record("store_secret", secret=name)
        self.secrets[name] = value
        return True

    def load_secret(self, name: str) -> str | None:
        """The secret stored under ``name``, or an empty string."""
        self._record("load_secret", secret=name)
        return self.secrets.get(name)

    def delete_secret(self, name: str) -> bool:
        """Forget the secret and report whether it was there."""
        self._record("delete_secret", secret=name)
        return self.secrets.pop(name, None) is not None

    def device_language(self) -> str:
        """The language tag this stub was configured for."""
        return self.language


class StubAudioPlayer:
    """A player that behaves like the real one but makes no sound.

    Position advances with the clock rather than instantly, so a test that
    waits a fraction of a second sees a plausible ``on_tick`` sequence, and
    ``duration`` is read from the file when it is a real WAV on disk.
    """

    duration_ms = 1000

    def __init__(
        self,
        source: str,
        *,
        on_end: Any = None,
        on_error: Any = None,
        volume: float = 1.0,
        looping: bool = False,
    ) -> None:
        self.source = source
        self.on_end = on_end
        self.on_error = on_error
        self.volume = volume
        self.looping = looping
        self.released = False
        self.started_at = 0.0
        self.offset = 0
        self.duration_ms = _wav_duration_ms(source)

    def _now(self) -> int:
        return self.offset + int((time.monotonic() - self.started_at) * 1000)

    def play(self) -> None:
        """Start the fake clock: time advances, but nothing is heard."""
        self.started_at = time.monotonic()

    def pause(self) -> None:
        """Freeze the clock and remember the position."""
        self.offset = self._now()

    def stop(self) -> None:
        """Freeze the clock and rewind to the start."""
        self.offset = 0

    def seek(self, position_ms: int) -> None:
        """Move the position to ``position_ms``, clamped to the clip."""
        self.offset = max(0, int(position_ms))

    def position(self) -> int:
        """How far the clip has played, from the fake clock."""
        return self._now()

    def duration(self) -> int:
        """The length of the clip, as the stub was told it."""
        return self.duration_ms

    def is_playing(self) -> bool:
        """Whether the fake clock is running."""
        return not self.released and self._now() < self.duration_ms

    def set_looping(self, looping: bool) -> None:
        """Remember whether playback should repeat."""
        self.looping = bool(looping)

    def set_volume(self, volume: float) -> None:
        """Remember the volume, clamped to 0.0..1.0."""
        self.volume = float(volume)

    def release(self) -> None:
        """Stop playback and free the fake player."""
        self.released = True


class StubAudioRecorder:
    """Writes a tiny placeholder file so ``stop()`` returns a real path."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.started_at = 0.0
        self.active = False
        self.target: Path | None = None

    def start(self) -> None:
        """Create an empty file in place of a recording and start the clock."""
        self.started_at = time.monotonic()
        self.active = True
        self.directory.mkdir(parents=True, exist_ok=True)
        self.target = self.directory / "recording-1.m4a"
        self.target.write_bytes(b"pymobile-stub-recording")

    def is_recording(self) -> bool:
        """Whether the fake recorder has been started and not stopped."""
        return self.active

    def elapsed_ms(self) -> int:
        """How long the fake recording has been running."""
        if not self.active:
            return 0
        return int((time.monotonic() - self.started_at) * 1000)

    def stop(self) -> str:
        """Stop the clock and return the path of the empty file."""
        self.active = False
        return str(self.target) if self.target is not None else ""

    def cancel(self) -> None:
        """Stop the clock and delete the empty file."""
        self.active = False
        if self.target is not None:
            self.target.unlink(missing_ok=True)
        self.target = None


def _wav_duration_ms(path: str) -> int:
    """Length of a PCM WAV file, or the default when it is not one."""
    try:
        import wave

        with wave.open(path, "rb") as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
        if rate:
            return int(frames / rate * 1000)
    except Exception:
        pass
    return StubAudioPlayer.duration_ms
