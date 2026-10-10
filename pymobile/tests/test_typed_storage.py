"""Tests for the optional typed model adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from pymobile import ModelStore, ModelStoreError, Storage


@dataclass(frozen=True)
class Plant:
    id: str
    name: str
    watered_on: str = ""


def test_dataclass_model_store_crud_and_schema_envelope(tmp_path) -> None:
    storage = Storage(tmp_path / "store.json")
    plants = ModelStore(storage, Plant, key="plants", schema_version=1)
    fern = Plant("fern-1", "Fern", "2026-10-10")

    assert plants.all() == []
    assert plants.put(fern) is fern
    assert plants.get("fern-1") == fern
    assert plants.list() == [fern]
    assert len(plants) == 1
    assert storage.get("plants") == {
        "schema_version": 1,
        "records": [{"id": "fern-1", "name": "Fern", "watered_on": "2026-10-10"}],
    }

    changed = Plant("fern-1", "Boston fern")
    plants.put(changed)
    assert plants.all() == [changed]
    assert plants.delete("fern-1") is True
    assert plants.delete("fern-1") is False
    assert plants.all() == []


def test_schema_migration_runs_in_order_and_is_persisted(tmp_path) -> None:
    storage = Storage(tmp_path / "store.json")
    storage.set(
        "plants",
        {"schema_version": 1, "records": [{"id": "p1", "name": "Fern"}]},
    )
    seen: list[str] = []

    def migrate_v1(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen.append("v1")
        return [{**record, "watered_on": ""} for record in records]

    store = ModelStore(storage, Plant, key="plants", schema_version=2, migrations={1: migrate_v1})
    assert store.get("p1") == Plant("p1", "Fern")
    assert seen == ["v1"]
    assert storage.get("plants") == {
        "schema_version": 2,
        "records": [{"id": "p1", "name": "Fern", "watered_on": ""}],
    }
    # The migration is committed once; later reads use the current schema.
    assert store.all() == [Plant("p1", "Fern")]
    assert seen == ["v1"]


def test_missing_or_future_schema_is_never_reset(tmp_path) -> None:
    storage = Storage(tmp_path / "store.json")
    legacy = {"schema_version": 1, "records": [{"id": "p1", "name": "Fern"}]}
    storage.set("plants", legacy)
    current = ModelStore(storage, Plant, key="plants", schema_version=2)
    with pytest.raises(ModelStoreError, match="needs a migration"):
        current.all()
    assert storage.get("plants") == legacy

    storage.set("plants", {"schema_version": 3, "records": []})
    with pytest.raises(ModelStoreError, match="newer than this app"):
        current.all()
    assert storage.get("plants") == {"schema_version": 3, "records": []}


def test_custom_converter_supports_non_json_model_fields(tmp_path) -> None:
    @dataclass(frozen=True)
    class Watering:
        id: str
        day: date

    def encode(item: Watering) -> dict[str, str]:
        return {"id": item.id, "day": item.day.isoformat()}

    def decode(record: Any) -> Watering:
        return Watering(str(record["id"]), date.fromisoformat(str(record["day"])))

    store = ModelStore(
        Storage(tmp_path / "store.json"),
        Watering,
        key="watering",
        to_record=encode,
        from_record=decode,
    )
    value = Watering("water-1", date(2026, 10, 10))
    store.put(value)
    assert store.get("water-1") == value


def test_rejects_duplicate_ids_and_non_json_records(tmp_path) -> None:
    storage = Storage(tmp_path / "store.json")
    storage.set(
        "plants",
        {
            "schema_version": 1,
            "records": [
                {"id": "same", "name": "Fern"},
                {"id": "same", "name": "Rose"},
            ],
        },
    )
    store = ModelStore(storage, Plant, key="plants")
    with pytest.raises(ModelStoreError, match="duplicate"):
        store.all()

    @dataclass
    class Unsupported:
        id: str
        payload: object

    unsupported = ModelStore(Storage(tmp_path / "unsupported.json"), Unsupported, key="bad")
    with pytest.raises(ModelStoreError, match="not JSON serializable"):
        unsupported.put(Unsupported("bad", object()))


def test_migration_failure_rolls_back_the_underlying_transaction(tmp_path) -> None:
    storage = Storage(tmp_path / "store.json")
    legacy = {"schema_version": 1, "records": [{"id": "p1", "name": "Fern"}]}
    storage.set("plants", legacy)

    def fail(_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise RuntimeError("bad migration")

    store = ModelStore(storage, Plant, key="plants", schema_version=2, migrations={1: fail})
    with pytest.raises(ModelStoreError, match=r"Migration 1→2.*bad migration"):
        store.all()
    assert storage.get("plants") == legacy
