"""Optional typed model persistence on top of :class:`~.storage.Storage`.

``ModelStore`` keeps small dataclass models as JSON records in the existing
atomic key/value store. It does not replace ``Storage`` or introduce a database
dependency: it adds model conversion, basic CRUD and explicit schema
migrations for apps that have outgrown hand-written dictionaries.

A model needs a stable ``id`` field by default. Dataclasses are converted with
``dataclasses.asdict`` and reconstructed with ``Model(**record)``; pass custom
``to_record``/``from_record`` functions for nested or non-JSON values.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, Generic, TypeVar, cast

from ...errors import ModelStoreError
from .storage import Storage

__all__ = ["ModelStore", "ModelMigration"]

T = TypeVar("T")
ModelId = str | int
_Record = dict[str, Any]

#: A step takes all records at schema ``n`` and returns their schema ``n + 1``.
ModelMigration = Callable[[list[_Record]], list[Mapping[str, Any]]]


class ModelStore(Generic[T]):
    """A typed CRUD adapter over one key in :class:`Storage`.

    ``ModelStore`` stores one envelope under ``key``::

        {"schema_version": 1, "records": [{"id": "…", "name": "…"}]}

    The default converter supports dataclasses with JSON-compatible fields.
    If a model contains a ``date``, nested class, enum or another value that
    JSON cannot reconstruct by calling its constructor, provide both
    ``to_record=`` and ``from_record=``.

    ``migrations`` maps the *source* version to a function that upgrades the
    complete list of records by one version. For example, ``{1: migrate_v1}``
    handles the transition from schema 1 to schema 2. Migrations run in order
    and are written back atomically on the next read or write. A missing step
    or a store created by a newer app raises :class:`ModelStoreError`; data is
    never silently reset.

    Example::

        store = ModelStore(app.storage, Plant, key="plants", schema_version=1)
        store.put(Plant(id="fern", name="Fern"))
        plants = store.all()
        fern = store.get("fern")
        store.delete("fern")
    """

    __slots__ = (
        "_storage",
        "_model_type",
        "_key",
        "_schema_version",
        "_id_field",
        "_migrations",
        "_to_record",
        "_from_record",
    )

    def __init__(
        self,
        storage: Storage,
        model_type: type[T],
        *,
        key: str,
        schema_version: int = 1,
        id_field: str = "id",
        migrations: Mapping[int, ModelMigration] | None = None,
        to_record: Callable[[T], Mapping[str, Any]] | None = None,
        from_record: Callable[[Mapping[str, Any]], T] | None = None,
    ) -> None:
        if not isinstance(storage, Storage):
            raise TypeError("storage must be a pymobile.Storage instance")
        if not isinstance(model_type, type):
            raise TypeError("model_type must be a model class")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key must be a non-empty string")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        if not isinstance(id_field, str) or not id_field.strip():
            raise ValueError("id_field must be a non-empty string")
        if (to_record is None) != (from_record is None):
            raise ValueError("pass both to_record and from_record, or neither")
        if to_record is None and not is_dataclass(model_type):
            raise TypeError(
                "the default ModelStore converter requires a dataclass model; "
                "provide both to_record= and from_record= for another model type"
            )

        checked_migrations: dict[int, ModelMigration] = {}
        for source_version, migration in (migrations or {}).items():
            if (
                isinstance(source_version, bool)
                or not isinstance(source_version, int)
                or source_version < 1
                or source_version >= schema_version
            ):
                raise ValueError(
                    "migration keys must be source versions from 1 up to "
                    "schema_version - 1"
                )
            if not callable(migration):
                raise TypeError(f"migration for schema {source_version} must be callable")
            checked_migrations[source_version] = migration

        self._storage = storage
        self._model_type = model_type
        self._key = key
        self._schema_version = schema_version
        self._id_field = id_field
        self._migrations = checked_migrations
        self._to_record = to_record
        self._from_record = from_record

    @property
    def key(self) -> str:
        """The key in the underlying :class:`Storage`."""
        return self._key

    @property
    def schema_version(self) -> int:
        """The schema version this adapter reads and writes."""
        return self._schema_version

    @property
    def model_type(self) -> type[T]:
        """The class returned by :meth:`all` and :meth:`get`."""
        return self._model_type

    def _identifier(self, record: Mapping[str, Any], *, context: str) -> ModelId:
        identifier = record.get(self._id_field)
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
            raise ModelStoreError(
                f"{context} needs a non-empty string or integer {self._id_field!r} field"
            )
        if isinstance(identifier, str) and not identifier.strip():
            raise ModelStoreError(f"{context} has an empty {self._id_field!r} field")
        return identifier

    @staticmethod
    def _json_record(record: Mapping[str, Any], *, context: str) -> _Record:
        if not isinstance(record, Mapping):
            raise ModelStoreError(f"{context} must be a mapping of field names to values")
        if any(not isinstance(name, str) for name in record):
            raise ModelStoreError(f"{context} field names must be strings")
        try:
            converted = json.loads(
                json.dumps(dict(record), ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError) as exc:
            raise ModelStoreError(
                f"{context} is not JSON serializable: {exc}",
                hint=(
                    "Use JSON-compatible fields or provide to_record=/from_record= "
                    "converters (for example, convert dates to ISO strings)."
                ),
            ) from exc
        if not isinstance(converted, dict):  # defensive: dict(record) is an object
            raise ModelStoreError(f"{context} did not encode to a JSON object")
        return cast(_Record, converted)

    def _validate_records(self, records: object, *, context: str) -> list[_Record]:
        if not isinstance(records, list):
            raise ModelStoreError(f"{context} must be a list of records")
        result: list[_Record] = []
        seen: set[tuple[type, ModelId]] = set()
        for index, raw_record in enumerate(records):
            record = self._json_record(raw_record, context=f"{context} record {index}")
            identifier = self._identifier(record, context=f"{context} record {index}")
            identity = (type(identifier), identifier)
            if identity in seen:
                raise ModelStoreError(
                    f"{context} contains duplicate {self._id_field} {identifier!r}"
                )
            seen.add(identity)
            result.append(record)
        return result

    def _read_records(self) -> list[_Record]:
        """Read, validate and migrate records; caller holds Storage's transaction."""
        if not self._storage.contains(self._key):
            return []
        envelope = self._storage.get(self._key)
        if not isinstance(envelope, Mapping):
            raise ModelStoreError(
                f"Model store {self._key!r} must contain a schema envelope",
                hint="The value was left untouched; inspect the storage file before repairing it.",
            )
        version = envelope.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ModelStoreError(
                f"Model store {self._key!r} has an invalid schema_version {version!r}"
            )
        if version > self._schema_version:
            raise ModelStoreError(
                f"Model store {self._key!r} uses schema {version}, newer than this app's "
                f"schema {self._schema_version}",
                hint=(
                    "Upgrade the app or add a reader for this newer schema; "
                    "the records were not changed."
                ),
            )
        records = self._validate_records(
            envelope.get("records"), context=f"model store {self._key!r}"
        )
        migrated = False
        while version < self._schema_version:
            migration = self._migrations.get(version)
            if migration is None:
                raise ModelStoreError(
                    f"Model store {self._key!r} needs a migration from schema {version} "
                    f"to {version + 1}",
                    hint=f"Pass migrations={{{version}: migrate_v{version}}} to ModelStore.",
                )
            try:
                new_records = migration([dict(record) for record in records])
                records = self._validate_records(
                    new_records,
                    context=f"migration {version}→{version + 1} for {self._key!r}",
                )
            except ModelStoreError:
                raise
            except Exception as exc:
                raise ModelStoreError(
                    f"Migration {version}→{version + 1} for {self._key!r} failed: {exc}"
                ) from exc
            version += 1
            migrated = True

        if migrated:
            self._storage.set(
                self._key,
                {"schema_version": self._schema_version, "records": records},
            )
        return records

    def _encode(self, model: T) -> _Record:
        if not isinstance(model, self._model_type):
            raise TypeError(
                f"expected {self._model_type.__name__}, got {type(model).__name__}"
            )
        try:
            source = (
                self._to_record(model)
                if self._to_record is not None
                else asdict(cast(Any, model))
            )
        except Exception as exc:
            raise ModelStoreError(
                f"Could not convert {self._model_type.__name__} to a record: {exc}"
            ) from exc
        record = self._json_record(source, context=f"{self._model_type.__name__} record")
        self._identifier(record, context=f"{self._model_type.__name__} record")
        return record

    def _decode(self, record: Mapping[str, Any]) -> T:
        try:
            if self._from_record is not None:
                model = self._from_record(record)
            else:
                model = self._model_type(**dict(record))
        except Exception as exc:
            raise ModelStoreError(
                f"Could not restore {self._model_type.__name__} from a stored record: {exc}",
                hint="Check the model fields or supply a matching from_record= converter.",
            ) from exc
        if not isinstance(model, self._model_type):
            raise ModelStoreError(
                f"from_record returned {type(model).__name__}, expected {self._model_type.__name__}"
            )
        return cast(T, model)

    def all(self) -> list[T]:
        """Return every stored model in insertion order."""
        with self._storage.transaction():
            return [self._decode(record) for record in self._read_records()]

    def list(self) -> list[T]:
        """Alias for :meth:`all`."""
        return self.all()

    def get(self, identifier: ModelId) -> T | None:
        """Return a model by id, or ``None`` when it does not exist."""
        self._validate_lookup_id(identifier)
        with self._storage.transaction():
            for record in self._read_records():
                stored_id = self._identifier(record, context="stored record")
                if type(stored_id) is type(identifier) and stored_id == identifier:
                    return self._decode(record)
        return None

    def put(self, model: T) -> T:
        """Insert or replace a model, identified by ``id_field``."""
        record = self._encode(model)
        identifier = self._identifier(record, context=f"{self._model_type.__name__} record")
        with self._storage.transaction() as storage:
            records = self._read_records()
            for index, stored in enumerate(records):
                stored_id = self._identifier(stored, context="stored record")
                if type(stored_id) is type(identifier) and stored_id == identifier:
                    records[index] = record
                    break
            else:
                records.append(record)
            storage.set(
                self._key,
                {"schema_version": self._schema_version, "records": records},
            )
        return model

    def delete(self, identifier: ModelId) -> bool:
        """Delete a model by id; return ``False`` when the id was absent."""
        self._validate_lookup_id(identifier)
        with self._storage.transaction() as storage:
            records = self._read_records()
            remaining = [
                record
                for record in records
                if not (
                    type(self._identifier(record, context="stored record")) is type(identifier)
                    and self._identifier(record, context="stored record") == identifier
                )
            ]
            if len(remaining) == len(records):
                return False
            storage.set(
                self._key,
                {"schema_version": self._schema_version, "records": remaining},
            )
            return True

    def clear(self) -> None:
        """Remove all records while keeping an empty envelope at the current schema."""
        with self._storage.transaction() as storage:
            storage.set(self._key, {"schema_version": self._schema_version, "records": []})

    def __len__(self) -> int:
        with self._storage.transaction():
            return len(self._read_records())

    @staticmethod
    def _validate_lookup_id(identifier: ModelId) -> None:
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
            raise TypeError("model id must be a string or integer (not bool)")
        if isinstance(identifier, str) and not identifier.strip():
            raise ValueError("model id must not be empty")
