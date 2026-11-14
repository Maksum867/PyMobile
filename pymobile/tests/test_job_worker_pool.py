"""Background jobs: a fixed worker pool instead of a thread per job.

Five hundred jobs used to mean five hundred threads. ``JobManager`` now runs
one-shot jobs on a bounded pool and keeps a dedicated thread only for
repeating jobs, whose loop *is* the job.
"""

from __future__ import annotations

import threading
import time

import pytest

from pymobile import JobManager


class TestWorkerPool:
    def test_many_jobs_do_not_create_a_thread_each(self) -> None:
        manager = JobManager(max_workers=4)
        started = threading.active_count()
        for _ in range(100):
            manager.enqueue(lambda: time.sleep(0.01))
        try:
            # Four workers, not a hundred threads.
            assert threading.active_count() <= started + 6
            assert manager.workers == 4
        finally:
            manager.shutdown()

    def test_every_job_still_runs(self) -> None:
        seen: list[int] = []
        manager = JobManager(max_workers=3)
        lock = threading.Lock()

        def job(index: int) -> None:
            with lock:
                seen.append(index)

        try:
            handles = [manager.enqueue(lambda index=index: job(index)) for index in range(50)]
            for handle in handles:
                handle.wait(timeout=10)
        finally:
            manager.shutdown()
        assert sorted(seen) == list(range(50))

    def test_the_pool_size_is_validated(self) -> None:
        with pytest.raises(ValueError, match="max_workers must be >= 1"):
            JobManager(max_workers=0)
        with pytest.raises(TypeError, match="max_workers must be an int"):
            JobManager(max_workers="four")  # type: ignore[arg-type]

    def test_a_repeating_job_keeps_its_own_thread(self) -> None:
        ticks: list[int] = []
        manager = JobManager(max_workers=1)
        handle = manager.every(10, lambda: ticks.append(1))
        try:
            deadline = time.monotonic() + 2
            while len(ticks) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(ticks) >= 2
            # The single worker is still free for one-shot work: a repeating
            # job must not park it in ``wait(interval)``.
            manager.enqueue(lambda: ticks.append(99)).wait(timeout=5)
            assert 99 in ticks
        finally:
            handle.cancel()
            manager.shutdown()

    def test_shutdown_stops_accepting_jobs(self) -> None:
        manager = JobManager(max_workers=2)
        manager.shutdown()
        with pytest.raises(RuntimeError, match="shut down"):
            manager.enqueue(lambda: None)

    def test_shutdown_cancels_jobs_that_never_started(self) -> None:
        ran: list[int] = []
        manager = JobManager(max_workers=1)
        blocker = threading.Event()
        manager.enqueue(lambda: blocker.wait(5))
        for _ in range(5):
            manager.enqueue(lambda: ran.append(1))
        manager.shutdown()  # cancels the queue, exactly like cancel_all()
        blocker.set()
        time.sleep(0.1)
        assert ran == []

    def test_shutdown_ends_the_worker_threads(self) -> None:
        before = {thread.name for thread in threading.enumerate()}
        manager = JobManager(max_workers=2)
        manager.enqueue(lambda: None).wait(timeout=5)
        manager.shutdown(wait=True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (
                not {
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name.startswith("pymobile-worker-")
                }
                - before
            ):
                return
            time.sleep(0.01)
        pytest.fail("worker threads survived shutdown")

    def test_a_cancelled_job_that_never_started_does_not_run(self) -> None:
        ran: list[int] = []
        manager = JobManager(max_workers=1)
        blocker = threading.Event()
        try:
            manager.enqueue(lambda: blocker.wait(5))
            handle = manager.enqueue(lambda: ran.append(1))
            handle.cancel()
            blocker.set()
            handle.wait(timeout=5)
            time.sleep(0.1)
            assert ran == []
        finally:
            blocker.set()
            manager.shutdown()
