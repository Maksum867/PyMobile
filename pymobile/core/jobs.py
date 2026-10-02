"""Background jobs (a lightweight WorkManager-style API).

The scheduler runs callbacks on a background thread but does not track "jobs"
that need to be remembered, cancelled by name, or observed when they finish.
:class:`JobManager` layers that on top: you enqueue a job, get back a
:class:`JobHandle`, and can cancel it by id or wait for its result. Jobs that
run repeatedly and one-shot jobs are both supported.

Jobs fire on a daemon thread, so the UI is never blocked. An exception inside a
job is captured on the handle and delivered to ``on_error`` (if any) rather than
crashing the app. All jobs are cancelled automatically when the app stops.

``then()`` callbacks run on the job's worker thread. To change widgets from
them, hand the update to the UI with :meth:`App.dispatch`. An exception raised
by a callback is logged with its traceback and passed to that callback's
``on_error``; it never changes the job's own result.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from ..log import get_logger

__all__ = ["JobManager", "JobHandle", "job_id"]

_log = get_logger("jobs")


def job_id() -> str:
    """Return a unique, human-friendly job id."""
    return uuid.uuid4().hex[:12]


class JobHandle:
    """A handle to an enqueued background job.

    ``done``/``result``/``error`` expose the outcome; ``cancel()`` stops a
    pending or repeating job and clears its callbacks. ``wait()`` blocks until
    the job finishes and returns its result (raising the job's error if any).

    A cancelled job is ``done`` only once it has actually stopped: Python
    cannot interrupt a function that is already running, so ``cancel()``
    suppresses the callbacks and stops a repeating job after the current run,
    and ``done`` turns true when the worker returns.
    """

    __slots__ = (
        "id",
        "_cancel_fn",
        "_done",
        "_result",
        "_error",
        "_lock",
        "_cancelled",
        "_callbacks",
        "_deliver",
    )

    def __init__(
        self,
        job_id: str,
        cancel_fn: Callable[[], None],
        deliver: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.id = job_id
        #: Runs completion callbacks; App passes one that moves them to the
        #: UI side. None: they run on the worker thread.
        self._deliver = deliver
        self._cancel_fn = cancel_fn
        self._done = threading.Event()
        self._result: Any = None
        self._error: BaseException | None = None
        self._lock = threading.Lock()
        self._cancelled = False
        self._callbacks: list[Callable[[], None]] = []

    @property
    def done(self) -> bool:
        """Whether the job has finished running (also after a cancellation)."""
        return self._done.is_set()

    @property
    def cancelled(self) -> bool:
        """Whether :meth:`cancel` was called."""
        return self._cancelled

    def cancel(self) -> None:
        """Cancel the job (if not finished) and suppress its callbacks."""
        with self._lock:
            self._cancelled = True
            self._callbacks.clear()
        # ``done`` is not set here: the worker may still be running. It is set
        # by _finish_cancelled() / _complete() when the worker really stops.
        self._cancel_fn()

    @property
    def result(self) -> Any:
        """The job's return value, or ``None`` until it finishes successfully."""
        return self._result

    @property
    def error(self) -> BaseException | None:
        """The exception the job raised, if any."""
        return self._error

    def then(
        self,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        *,
        on_success: Callable[[Any], None] | None = None,
    ) -> JobHandle:
        """Register callbacks for completion; run immediately if already done.

        ``on_success`` is a documented alias of the positional ``on_done``
        argument so the same keyword works on :class:`~pymobile.core.net.http.HttpFuture`.

        You may pass ``on_error`` without ``on_done``/``on_success`` to handle
        only failures — the success value is silently discarded.

        Cancelling the handle suppresses the callbacks even when one of them is
        already sitting in the delivery queue: the wrapper re-checks
        ``cancelled`` right before it runs.
        """
        if on_done is not None and on_success is not None:
            raise TypeError("pass either on_done or on_success, not both")
        done = on_done if on_done is not None else on_success
        if done is None and on_error is None:
            raise TypeError("then() requires at least on_done, on_success, or on_error")
        callback: Callable[[], None] | None = None
        with self._lock:
            if self._cancelled:
                return self

            def callback() -> None:
                # Re-check at delivery time: cancel() may have run after this
                # wrapper was queued, and the documented contract is that a
                # cancelled job fires no callbacks at all.
                if self._cancelled:
                    return
                self._fire(done, on_error)

            if not self._done.is_set():
                self._callbacks.append(callback)
                return self
        # Never call application code with _lock held: a callback may cancel
        # this handle or register another callback.
        assert callback is not None
        self._run(callback)
        return self

    def _run(self, callback: Callable[[], None]) -> None:
        if self._deliver is None:
            callback()
        else:
            self._deliver(callback)

    def wait(self, timeout: float | None = None) -> Any:
        """Block until the job finishes and return its result.

        Raises :class:`TimeoutError` when ``timeout`` seconds elapse before the
        job completes. Raises the job's own exception if it failed.
        """
        finished = self._done.wait(timeout)
        if not finished:
            raise TimeoutError(
                f"Job {self.id} did not complete"
                + (f" within {timeout} seconds" if timeout is not None else "")
            )
        if self._error is not None:
            raise self._error
        return self._result

    def _fire(
        self,
        on_done: Callable[[Any], None] | None,
        on_error: Callable[[BaseException], None] | None,
    ) -> None:
        if self._error is not None:
            if on_error is not None:
                try:
                    on_error(self._error)
                except Exception:
                    _log.exception("on_error callback of job %r failed", self.id)
            return
        if on_done is None:
            return
        try:
            on_done(self._result)
        except Exception as exc:
            # This used to escape into the worker, which then recorded the
            # callback's exception as the job's error with result=None.
            _log.exception("on_success callback of job %r failed", self.id)
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception:
                    _log.exception("on_error callback of job %r failed", self.id)

    def _complete(self, result: Any, error: BaseException | None) -> None:
        with self._lock:
            if self._cancelled:
                # Keep the outcome for wait()/result, but no callbacks.
                self._result = result
                self._error = error
                self._done.set()
                return
            self._result = result
            self._error = error
            self._done.set()
            callbacks = list(self._callbacks)
            self._callbacks.clear()
        # User callbacks intentionally run after releasing _lock; _fire()
        # contains their exceptions, so one failing callback cannot hide others.
        for cb in callbacks:
            self._run(cb)

    def _finish_cancelled(self) -> None:
        """The worker stopped before running the job at all."""
        with self._lock:
            self._done.set()


class JobManager:
    """Enqueues and tracks background jobs.

    ``enqueue`` runs ``fn`` on a daemon thread. ``every`` runs ``fn`` on an
    interval until cancelled. ``cancel_all`` stops everything (called by
    :meth:`App.stop` integration).
    """

    __slots__ = ("_jobs", "_lock", "_alive", "_deliver")

    def __init__(self, *, deliver: Callable[[Callable[[], None]], None] | None = None) -> None:
        """``deliver(callback)`` runs ``then()`` callbacks — :class:`App`
        passes one that runs them on the UI side; by default they run on the
        worker thread."""
        self._deliver = deliver
        self._jobs: dict[str, JobHandle] = {}
        self._lock = threading.Lock()
        self._alive = True

    @property
    def active(self) -> list[JobHandle]:
        """Handles for jobs that are neither finished nor cancelled."""
        with self._lock:
            return [h for h in self._jobs.values() if not h.done and not h.cancelled]

    def enqueue(self, fn: Callable[[], Any], *, name: str | None = None) -> JobHandle:
        """Run ``fn`` once on a background thread; returns a :class:`JobHandle`."""
        if not self._alive:
            raise RuntimeError("JobManager has been shut down")
        handle_id = name or job_id()

        cancelled = threading.Event()

        def run() -> None:
            try:
                if cancelled.is_set() or handle.cancelled:
                    handle._finish_cancelled()
                    return
                # Only fn() itself may produce the job's error; callbacks run
                # afterwards and handle their own exceptions.
                try:
                    result = fn()
                except BaseException as error:
                    handle._complete(None, error)
                else:
                    handle._complete(result, None)
            finally:
                with self._lock:
                    # Only clear our own slot: a second job enqueued under the
                    # same name overwrites the dictionary entry, and the first
                    # completing one must not evict it.
                    if self._jobs.get(handle_id) is handle:
                        self._jobs.pop(handle_id, None)

        handle = JobHandle(handle_id, cancel_fn=cancelled.set, deliver=self._deliver)
        self._track(handle)
        threading.Thread(target=run, name=f"pymobile-job-{handle_id}", daemon=True).start()
        return handle

    def every(
        self,
        interval_ms: int,
        fn: Callable[[], Any],
        *,
        name: str | None = None,
    ) -> JobHandle:
        """Run ``fn`` every ``interval_ms`` until cancelled; returns a handle."""
        if not self._alive:
            raise RuntimeError("JobManager has been shut down")
        if interval_ms <= 0:
            raise ValueError("interval_ms must be > 0")
        handle_id = name or job_id()
        stop = threading.Event()

        def run() -> None:
            error: BaseException | None = None
            try:
                while not stop.is_set():
                    start = time.monotonic()
                    fn()
                    elapsed = (time.monotonic() - start) * 1000
                    remaining = interval_ms - elapsed
                    if remaining > 0:
                        stop.wait(remaining / 1000.0)
            except BaseException as exc:  # delivered through the handle
                error = exc
                _log.exception("repeating job %r failed; stopping", handle_id)
            finally:
                stop.set()
                handle._complete(None, error)
                with self._lock:
                    if self._jobs.get(handle_id) is handle:
                        self._jobs.pop(handle_id, None)

        handle = JobHandle(handle_id, cancel_fn=stop.set, deliver=self._deliver)
        self._track(handle)
        threading.Thread(target=run, name=f"pymobile-job-{handle_id}", daemon=True).start()
        return handle

    def _track(self, handle: JobHandle) -> None:
        """Register ``handle``, replacing — and cancelling — an older same-name job.

        Two ``every(..., name="sync")`` calls used to leave two workers running
        while ``_jobs["sync"]`` kept only the second one: the first was
        untracked, so ``shutdown()``/``cancel_all()`` never stopped it and its
        side effects outlived the app. An explicit name now means "at most one
        live job": the previous one is cancelled (loudly) instead of orphaned.
        """
        with self._lock:
            previous = self._jobs.get(handle.id)
            self._jobs[handle.id] = handle
        if previous is not None and not previous.done and not previous.cancelled:
            _log.warning(
                "job %r already exists and is being replaced; the previous one is cancelled",
                handle.id,
            )
            previous.cancel()

    def cancel(self, handle_id: str) -> bool:
        """Cancel a job by id; returns whether it was found."""
        with self._lock:
            handle = self._jobs.get(handle_id)
        if handle is not None:
            handle.cancel()
            return True
        return False

    def cancel_all(self) -> None:
        """Cancel every pending/repeating job."""
        with self._lock:
            handles = list(self._jobs.values())
        for handle in handles:
            handle.cancel()

    def shutdown(self) -> None:
        """Stop accepting new jobs and cancel all pending ones."""
        self._alive = False
        self.cancel_all()
