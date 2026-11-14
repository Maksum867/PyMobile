"""Storage write batching: fewer file rewrites, nothing lost.

A store with thousands of keys used to rewrite the whole JSON document on every
``set`` (about 1 ms, 12.9 s for 4000 writes). The deferred store batches them;
these tests pin both the speed and the fact that no write goes missing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pymobile import Storage
from pymobile.errors import PyMobileError


def _populate(store: Storage, count: int) -> None:
    for index in range(count):
        store.set(f"key{index}", index)


class TestDeferredWrites:
    def test_a_burst_costs_far_less_than_a_write_each(self, tmp_path: Path) -> None:
        def burst(store: Storage) -> float:
            _populate(store, 3000)
            store.save()
            started = time.monotonic()
            for index in range(500):
                store.set(f"key{index}", index)
            return time.monotonic() - started

        batched = burst(Storage(tmp_path / "batched.json", deferred=True))
        written = burst(Storage(tmp_path / "written.json"))
        # Measured on this machine: ~0.2 ms per write batched against ~1 ms
        # written through. The assertion leaves room for a slow CI box but would
        # fail outright if the batching ever went away.
        assert batched * 2 < written

    def test_the_batched_writes_reach_the_file(self, tmp_path: Path) -> None:
        store = Storage(tmp_path / "store.json", deferred=True)
        store.set("a", 1)
        store.set("b", 2)
        store.flush()
        reloaded = Storage(tmp_path / "store.json")
        assert reloaded.get("a") == 1 and reloaded.get("b") == 2

    def test_save_writes_a_pending_change_immediately(self, tmp_path: Path) -> None:
        store = Storage(tmp_path / "store.json", deferred=True)
        store.set("a", 1)
        store.save()
        assert json.loads((tmp_path / "store.json").read_text()) == {"a": 1}

    def test_a_transaction_commits_immediately(self, tmp_path: Path) -> None:
        store = Storage(tmp_path / "store.json", deferred=True)
        with store.transaction():
            store.set("a", 1)
            store.set("b", 2)
        assert json.loads((tmp_path / "store.json").read_text()) == {"a": 1, "b": 2}

    def test_the_timer_flushes_on_its_own(self, tmp_path: Path) -> None:
        store = Storage(tmp_path / "store.json", deferred=True, defer_seconds=0.01)
        store.set("a", 1)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (tmp_path / "store.json").exists():
                break
            time.sleep(0.01)
        assert json.loads((tmp_path / "store.json").read_text()) == {"a": 1}

    def test_a_script_that_never_saves_still_writes(self, tmp_path: Path) -> None:
        target = tmp_path / "script.json"
        code = (
            "from pymobile import Storage\n"
            f"store = Storage({str(target)!r}, deferred=True)\n"
            "store.set('typed', 'and never saved')\n"
        )
        subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
        assert json.loads(target.read_text()) == {"typed": "and never saved"}

    def test_the_default_store_still_writes_every_change(self, tmp_path: Path) -> None:
        store = Storage(tmp_path / "store.json")
        store.set("a", 1)
        assert json.loads((tmp_path / "store.json").read_text()) == {"a": 1}

    def test_the_delay_is_validated(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="defer_seconds must be positive"):
            Storage(tmp_path / "s.json", deferred=True, defer_seconds=0)
        with pytest.raises(TypeError, match="defer_seconds must be a number"):
            Storage(tmp_path / "s.json", deferred=True, defer_seconds="soon")  # type: ignore[arg-type]

    def test_a_value_that_cannot_be_stored_is_refused_at_set_time(self, tmp_path: Path) -> None:
        store = Storage(tmp_path / "store.json", deferred=True)
        store.set("a", 1)
        # Deferring the write must not defer the validation: an app would
        # otherwise learn about a bad value only after the fact.
        with pytest.raises(PyMobileError, match="not JSON serializable"):
            store.set("bad", object())
        assert store.get("a") == 1
        assert store.get("bad") is None
