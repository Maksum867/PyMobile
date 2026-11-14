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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, Generic, TypeVar, cast

from ...errors import ModelStoreError
from ...log import get_logger
from .storage import Storage

__all__ = ["ModelStore", "ModelMigration", "Q", "ENVELOPE_WARN_BYTES"]

_log = get_logger("api.typed_storage")

T = TypeVar("T")
ModelId = str | int
_Record = dict[str, Any]

#: A step takes all records at schema ``n`` and returns their schema ``n + 1``.
ModelMigration = Callable[[list[_Record]], list[Mapping[str, Any]]]


class Q:
    """A query expression for :meth:`ModelStore.find`.

    Deliberately tiny: ``Q`` compares one field, ``&`` and ``|`` combine
    queries, ``~`` negates one. A dict passed to ``find`` means the same as an
    ``&`` of ``Q(field, "==", value)`` for every entry, which is what a caller
    writing ``store.find(lambda m: …)`` actually wanted most of the time.

    >>> Q("done", "==", False) & Q("priority", ">=", 2)
    """

    __slots__ = ("_text", "_predicate")
    _predicate: Callable[[Mapping[str, Any]], bool]
    _text: str

    def __init__(self, field: str, op: str, value: Any = None) -> None:
        if not isinstance(field, str) or not field.strip():
            raise ValueError("Q needs a field name")
        if op not in _OPERATORS:
            raise ValueError(f"unknown operator {op!r}; use one of {', '.join(sorted(_OPERATORS))}")
        compare = _OPERATORS[op]
        self._text = f"{field} {op} {value!r}"

        def predicate(record: Mapping[str, Any]) -> bool:
            if field not in record:
                return False
            try:
                return bool(compare(record[field], value))
            except TypeError:
                # Comparing 3 with "three" is a data question, not a crash:
                # the record simply does not match.
                return False

        self._predicate = predicate

    def __and__(self, other: Q) -> Q:
        return _Combined(
            lambda record: self.matches(record) and other.matches(record), f"({self} and {other})"
        )

    def __or__(self, other: Q) -> Q:
        return _Combined(
            lambda record: self.matches(record) or other.matches(record), f"({self} or {other})"
        )

    def __invert__(self) -> Q:
        return _Combined(lambda record: not self.matches(record), f"not ({self})")

    def matches(self, record: Mapping[str, Any]) -> bool:
        """Whether ``record`` satisfies this query."""
        return self._predicate(record)

    def __repr__(self) -> str:
        return f"Q({self._text})"


#: A warning threshold for one model store's envelope; beyond this, every write
#: serialises the whole list and the store should be split or moved to sqlite3.
ENVELOPE_WARN_BYTES = 1_000_000


def _check_envelope_overflow(key: str, by_id: Mapping[Any, Mapping[str, Any]]) -> None:
    """Warn once per store when the record list outgrows a single JSON value."""
    if len(by_id) % 500:
        return
    try:
        size = len(json.dumps(list(by_id.values())))
    except (TypeError, ValueError):  # pragma: no cover - records are JSON by contract
        return
    if size > ENVELOPE_WARN_BYTES:
        _log.warning(
            "model store %r holds %d records (~%.1f MB) in one JSON array: every "
            "write rewrites all of them; consider sqlite3 or splitting the store",
            key,
            len(by_id),
            size / 1_000_000,
        )


def _matches(
    query: object,
    record: Mapping[str, Any],
    decode: Callable[[Mapping[str, Any]], Any] | None = None,
) -> bool:
    """Apply a ``find`` query to one raw record."""
    if query is None:
        return True
    if isinstance(query, Q):
        return query.matches(record)
    if isinstance(query, Mapping):
        return all(
            _OPERATORS["=="](record.get(field, _MISSING), value) for field, value in query.items()
        )
    if callable(query):
        # A predicate on the *model* cannot be applied to a raw record: decode
        # the record to ask the question, which keeps dict/Q filters cheap and
        # lets a caller write the obvious lambda when it is clearer.
        return bool(query(decode(record) if decode is not None else record))
    raise TypeError("query must be a Q, a mapping of fields or a callable predicate")


def _sort_key(record: Mapping[str, Any], fields: list[str]) -> tuple[Any, ...]:
    """A total order across records, tolerating a missing or mixed-type field."""
    parts: list[tuple[int, Any]] = []
    for field in fields:
        value = record.get(field, _MISSING)
        if value is _MISSING:
            parts.append((1, ""))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append((0, value))
        else:
            parts.append((0, str(value)))
    return tuple(parts)


class _Combined(Q):
    """The result of ``&``, ``|`` or ``~``; kept separate to keep the slots tidy."""

    __slots__ = ()

    def __init__(self, predicate: Callable[[Mapping[str, Any]], bool], text: str) -> None:
        self._predicate = predicate
        self._text = text


_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
    "not in": lambda a, b: a not in b,
    "contains": lambda a, b: b in a,
    "startswith": lambda a, b: isinstance(a, str) and a.startswith(b),
    "endswith": lambda a, b: isinstance(a, str) and a.endswith(b),
}

#: Sentinel for "this field is absent" so a missing field never equals None.
_MISSING: Any = object()

Query = Q | Mapping[str, Any] | Callable[[T], bool]


class ModelStore(Generic[T]):
    """A typed CRUD adapter over one key in :class:`Storage`.

    ``ModelStore`` stores one envelope under ``key``::

        {"schema_version": 1, "records": [{"id": "…", "name": "…"}]}

    The default converter supports dataclasses with JSON-compatible fields.
    If a model contains a ``date``, nested class, enum or another value that
    JSON cannot reconstruct by calling its constructor, provide both
    ``to_record=`` and ``from_record=``.

    ``migrations`` maps the *source* version to a function, like
    ``Storage``'s ``{version: fn}`` is keyed by the version the store is
    already at — both mean "this is the function that runs when the stored data
    is at that version". ``{1: migrate_v1}`` upgrades v1 records to v2.
    handles the transition from schema 1 to schema 2. Migrations run in order
    and are written back atomically on the next read or write. A missing step
    or a store created by a newer app raises :class:`ModelStoreError`; data is
    never silently reset.

    ``get()``/``all()`` return copies, never live objects: a record lives in
    the storage as JSON, and a model is decoded from it on every read. Change a
    copy and write it back with :meth:`put`/:meth:`update`, or use
    :meth:`edit` to change fields in one call.

    Example::

        store = ModelStore(app.storage, Plant, key="plants", schema_version=1)
        store.put(Plant(id="fern", name="Fern"))
        plants = store.all()
        fern = store.get("fern")
        fern.watered = 2
        store.update(fern)             # persisted; the previous line changed a copy
        store.edit("fern", name="Boston fern")
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
        "_by_id",
        "_indexed",
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
                    "migration keys are the version the store is at "
                    "(1 up to schema_version - 1), the same convention as Storage"
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
        # id -> record, built once per store. Every read used to walk and
        # re-validate the whole list, so 1000 sequential puts cost five
        # seconds; ``put``/``delete`` now touch one entry and the cache is
        # dropped whenever the envelope on disk is replaced from outside.
        self._by_id: dict[Any, _Record] = {}
        self._indexed = False

    @property
    def storage(self) -> Storage:
        """The :class:`Storage` this store keeps its records in."""
        return self._storage

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
            converted = json.loads(json.dumps(dict(record), ensure_ascii=False, allow_nan=False))
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

    def _records(self) -> list[_Record]:
        """Every record, through the id cache; caller holds the transaction."""
        if not self._indexed:
            records = self._read_records()
            self._by_id = {
                self._identifier(record, context="stored record"): record for record in records
            }
            self._indexed = True
            return records
        return list(self._by_id.values())

    def _invalidate(self) -> None:
        self._indexed = False
        self._by_id = {}

    def find(
        self,
        query: Q | Mapping[str, Any] | Callable[[T], bool] | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
        order_by: str | Iterable[str] | None = None,
        reverse: bool = False,
    ) -> list[T]:
        """Return the models matching ``query``, decoded.

        ``query`` is a :class:`Q`, a mapping (all fields must match) or a plain
        predicate on the model. ``order_by`` names one or more record fields;
        ``limit``/``offset`` page the result. Filtering happens on the stored
        records — before decoding — so a page of ten does not build a thousand
        dataclasses.
        """
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool)):
            raise TypeError("limit must be an int or None")
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be an int >= 0")
        decode = self._decode if callable(query) and not isinstance(query, Q) else None
        with self._storage.transaction():
            records = self._records()
            matched = [record for record in records if _matches(query, record, decode)]
        if order_by is not None:
            fields = [order_by] if isinstance(order_by, str) else list(order_by)
            for field in fields:
                if not isinstance(field, str):
                    raise TypeError("order_by takes field names")
            matched.sort(key=lambda record: _sort_key(record, fields), reverse=reverse)
        window = matched[offset:] if limit is None else matched[offset : offset + limit]
        return [self._decode(record) for record in window]

    def count(self, query: Q | Mapping[str, Any] | Callable[[T], bool] | None = None) -> int:
        """How many models match ``query`` (``None`` counts everything).

        Counted on records, so it costs no decoding and no list of models.
        """
        decode = self._decode if callable(query) and not isinstance(query, Q) else None
        with self._storage.transaction():
            return sum(1 for record in self._records() if _matches(query, record, decode))

    def get_by(self, **fields: Any) -> T | None:
        """The first model whose fields equal ``fields``, or ``None``.

        The indexed shortcut for ``store.get(store.find({"email": email})[0].id)``.
        """
        if not fields:
            raise ValueError("get_by needs at least one field")
        found = self.find(fields, limit=1)
        return found[0] if found else None

    def _encode(self, model: T) -> _Record:
        if not isinstance(model, self._model_type):
            raise TypeError(f"expected {self._model_type.__name__}, got {type(model).__name__}")
        try:
            source = (
                self._to_record(model) if self._to_record is not None else asdict(cast(Any, model))
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
        return model

    def all(self) -> list[T]:
        """Return every stored model in insertion order."""
        with self._storage.transaction():
            return [self._decode(record) for record in self._records()]

    def list(self) -> list[T]:
        """Alias for :meth:`all`."""
        return self.all()

    def get(self, identifier: ModelId) -> T | None:
        """Return a model by id, or ``None`` when it does not exist."""
        self._validate_lookup_id(identifier)
        with self._storage.transaction():
            self._records()  # populates the id cache
            record = self._lookup(identifier)
            return None if record is None else self._decode(record)

    def put(self, model: T) -> T:
        """Insert or replace a model, identified by ``id_field``.

        ``all()`` and ``get()`` return **detached snapshots**: the dataclass is
        rebuilt from the stored JSON every time, so mutating it changes nothing
        until it is handed back to :meth:`put` (or :meth:`update`, which is the
        same call under the name the situation suggests)::

            card = store.get("c1")      # a copy — card.box == 1
            card.box += 1               # the store still says 1
            store.put(card)             # now it says 2
        """
        record = self._encode(model)
        identifier = self._identifier(record, context=f"{self._model_type.__name__} record")
        with self._storage.transaction() as storage:
            self._records()
            # One dict write instead of a list walk and a full re-validation:
            # this is what turns 1000 puts from five seconds into milliseconds.
            self._by_id[identifier] = record
            _check_envelope_overflow(self._key, self._by_id)
            storage.set(
                self._key,
                {
                    "schema_version": self._schema_version,
                    "records": list(self._by_id.values()),
                },
            )
        return model

    def update(self, model: T) -> T:
        """Save a model that was loaded, changed and is now ready to persist.

        An alias of :meth:`put` — the snapshot :meth:`get` returned has to be
        written back explicitly, and a call named ``update`` says that out
        loud at the place where it matters.
        """
        return self.put(model)

    def edit(self, identifier: ModelId, /, **changes: Any) -> T:
        """Load a model, apply ``changes``, persist and return it.

        The one-liner for the common "change one field" case, and the answer to
        a snapshot that looked live::

            store.edit("c1", box=2)     # stored and returned

        Raises :class:`~pymobile.errors.ModelStoreError` when the id is absent,
        a field does not exist on the model, or the model is frozen (then build
        a replacement with ``dataclasses.replace`` and pass it to
        :meth:`put`).
        """
        model = self.get(identifier)
        if model is None:
            raise ModelStoreError(
                f"no {self._model_type.__name__} with {self._id_field} {identifier!r} "
                f"in model store {self._key!r}",
                hint="Read it with get() first, or insert it with put().",
            )
        for name, value in changes.items():
            if not hasattr(model, name):
                known = ", ".join(sorted(vars(model))) or "none"
                raise ModelStoreError(
                    f"{self._model_type.__name__} has no field {name!r} (fields: {known})"
                )
            try:
                setattr(model, name, value)
            except Exception as exc:
                raise ModelStoreError(
                    f"could not set {name!r} on {self._model_type.__name__}: {exc}",
                    hint=(
                        "Frozen dataclasses cannot be edited in place: build a "
                        "replacement with dataclasses.replace(model, …) and call put()."
                    ),
                ) from exc
        return self.put(model)

    def delete(self, identifier: ModelId) -> bool:
        """Delete a model by id; return ``False`` when the id was absent."""
        self._validate_lookup_id(identifier)
        with self._storage.transaction() as storage:
            self._records()
            if self._lookup(identifier) is None:
                return False
            del self._by_id[identifier]
            storage.set(
                self._key,
                {
                    "schema_version": self._schema_version,
                    "records": list(self._by_id.values()),
                },
            )
            return True

    def clear(self) -> None:
        """Remove all records while keeping an empty envelope at the current schema."""
        with self._storage.transaction() as storage:
            self._invalidate()
            storage.set(self._key, {"schema_version": self._schema_version, "records": []})

    def __len__(self) -> int:
        with self._storage.transaction():
            return len(self._records())

    def _lookup(self, identifier: ModelId) -> _Record | None:
        """The raw record for ``identifier``, comparing types as before.

        ``True == 1`` in Python; a store keyed by strings must not hand back the
        record whose id is ``1`` for a lookup of ``True``.
        """
        record = self._by_id.get(identifier)
        if record is None:
            return None
        if type(record.get(self._id_field)) is not type(identifier):
            # ``True == 1`` and they hash alike, so the dict would happily hand
            # back the row whose id is ``1`` for a lookup of ``True``.
            return None
        return record

    @staticmethod
    def _validate_lookup_id(identifier: ModelId) -> None:
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
            raise TypeError("model id must be a string or integer (not bool)")
        if isinstance(identifier, str) and not identifier.strip():
            raise ValueError("model id must not be empty")
