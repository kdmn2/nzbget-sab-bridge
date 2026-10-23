"""Persistent, collision-free NZBGet integer ID mapping in SQLite."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

MAX_NZB_ID = 2_147_483_647
_VALID_STATES = frozenset({"unknown", "queue", "history"})
_SCHEMA_VERSION = 1


class IdMapError(RuntimeError):
    """Base class for safe ID-map failures."""


class IdMapUnavailable(IdMapError):
    """Storage is missing, malformed, or unwritable."""


class IdMapExhausted(IdMapError):
    """The signed 31-bit integer namespace is exhausted."""


def ensure_data_dir(path: Path) -> None:
    """Create and prove writability of the data directory.

    This intentionally does not chown or chmod a mounted directory. A fresh
    named volume inherits ownership from the image's /data directory; bind
    mounts must be prepared by the operator.
    """

    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
    except OSError as exc:
        raise IdMapUnavailable("data directory could not be created") from exc
    if not path.is_dir():
        raise IdMapUnavailable("data directory path is not a directory")
    if not os.access(path, os.W_OK | os.X_OK):
        raise IdMapUnavailable("data directory is not writable")
    probe = path / f".write-probe-{uuid.uuid4().hex}"
    try:
        descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        raise IdMapUnavailable("data directory is not writable") from exc
    else:
        os.close(descriptor)
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


class IdMap:
    """Transactional mapping between opaque SAB nzo_id strings and NZBGet IDs."""

    def __init__(
        self,
        path: Path,
        *,
        max_nzb_id: int = MAX_NZB_ID,
        busy_timeout_ms: int = 10_000,
    ) -> None:
        if max_nzb_id < 1 or max_nzb_id > MAX_NZB_ID:
            raise ValueError("max_nzb_id must be within the signed 31-bit range")
        self.path = path
        self.max_nzb_id = max_nzb_id
        self.busy_timeout_ms = busy_timeout_ms
        self._initialized = False

    def initialize(self) -> None:
        ensure_data_dir(self.path.parent)
        if self.path.exists() and self.path.is_dir():
            raise IdMapUnavailable("database path is a directory")
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS meta (
                        key TEXT PRIMARY KEY,
                        value INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS id_map (
                        nzb_id INTEGER PRIMARY KEY,
                        nzo_id TEXT NOT NULL UNIQUE,
                        created_at INTEGER NOT NULL,
                        last_seen_at INTEGER NOT NULL,
                        state TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_id_map_state_seen "
                    "ON id_map(state, last_seen_at)"
                )
                schema_row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()
                if schema_row is None:
                    connection.execute(
                        "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                        (_SCHEMA_VERSION,),
                    )
                elif schema_row["value"] != _SCHEMA_VERSION:
                    raise IdMapUnavailable("database schema version is unsupported")
                next_row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'next_id'"
                ).fetchone()
                if next_row is None:
                    connection.execute("INSERT INTO meta(key, value) VALUES('next_id', 1)")
                elif next_row["value"] < 1:
                    raise IdMapUnavailable("database ID sequence is invalid")
                integrity = connection.execute("PRAGMA quick_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise IdMapUnavailable("database integrity check failed")
        except IdMapError:
            raise
        except sqlite3.DatabaseError as exc:
            raise IdMapUnavailable("database is malformed or unreadable") from exc
        except OSError as exc:
            raise IdMapUnavailable("database could not be opened") from exc
        with suppress(OSError):
            os.chmod(self.path, 0o600)
        self._initialized = True

    def allocate(
        self,
        nzo_id: str,
        *,
        state: str = "unknown",
        now: int | None = None,
    ) -> int:
        return self.allocate_many([nzo_id], state=state, now=now)[0]

    def allocate_many(
        self,
        nzo_ids: list[str],
        *,
        state: str = "unknown",
        now: int | None = None,
        _race_attempt: int = 0,
    ) -> list[int]:
        self._check_initialized()
        self._validate_state(state)
        cleaned = [self._validate_nzo_id(item) for item in nzo_ids]
        timestamp = _now(now)
        assigned: list[int] = []
        try:
            with self._write_transaction() as connection:
                for nzo_id in cleaned:
                    row = connection.execute(
                        "SELECT nzb_id FROM id_map WHERE nzo_id = ?", (nzo_id,)
                    ).fetchone()
                    if row is not None:
                        connection.execute(
                            "UPDATE id_map SET last_seen_at = ?, state = ? WHERE nzo_id = ?",
                            (timestamp, state, nzo_id),
                        )
                        assigned.append(int(row["nzb_id"]))
                        continue

                    next_row = connection.execute(
                        "SELECT value FROM meta WHERE key = 'next_id'"
                    ).fetchone()
                    if next_row is None:
                        raise IdMapUnavailable("database ID sequence is missing")
                    next_id = int(next_row["value"])
                    if next_id > self.max_nzb_id:
                        raise IdMapExhausted("NZBGet integer ID space is exhausted")
                    connection.execute(
                        "INSERT INTO id_map(nzb_id, nzo_id, created_at, last_seen_at, state) "
                        "VALUES(?, ?, ?, ?, ?)",
                        (next_id, nzo_id, timestamp, timestamp, state),
                    )
                    connection.execute(
                        "UPDATE meta SET value = ? WHERE key = 'next_id'",
                        (next_id + 1,),
                    )
                    assigned.append(next_id)
        except IdMapError:
            raise
        except sqlite3.IntegrityError:
            # A concurrent writer can only be observed if a caller bypasses the
            # immediate write transaction; retry once to resolve the winner.
            if _race_attempt >= 1:
                raise IdMapUnavailable("concurrent allocation conflict") from None
            return self.allocate_many(
                cleaned,
                state=state,
                now=timestamp,
                _race_attempt=_race_attempt + 1,
            )
        except sqlite3.DatabaseError as exc:
            raise IdMapUnavailable("database write failed") from exc
        return assigned

    def get_nzb_id(self, nzo_id: str) -> int | None:
        self._check_initialized()
        self._validate_nzo_id(nzo_id)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT nzb_id FROM id_map WHERE nzo_id = ?", (nzo_id,)
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise IdMapUnavailable("database read failed") from exc
        return int(row["nzb_id"]) if row is not None else None

    def get_nzo_id(self, nzb_id: int) -> str | None:
        self._check_initialized()
        if not 1 <= nzb_id <= self.max_nzb_id:
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT nzo_id FROM id_map WHERE nzb_id = ?", (nzb_id,)
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise IdMapUnavailable("database read failed") from exc
        return str(row["nzo_id"]) if row is not None else None

    def mark_state(self, nzo_id: str, state: str, *, now: int | None = None) -> None:
        self._check_initialized()
        self._validate_nzo_id(nzo_id)
        self._validate_state(state)
        try:
            with self._write_transaction() as connection:
                connection.execute(
                    "UPDATE id_map SET state = ?, last_seen_at = ? WHERE nzo_id = ?",
                    (state, _now(now), nzo_id),
                )
        except sqlite3.DatabaseError as exc:
            raise IdMapUnavailable("database write failed") from exc

    def prune_history(
        self,
        *,
        retention_days: int,
        visible_nzo_ids: set[str],
        now: int | None = None,
    ) -> int:
        """Remove old history mappings only when explicitly configured.

        Queue and unknown mappings are never removed. A minimum retention of
        30 days is enforced to avoid invalidating IDs that the app can still
        display. The integer sequence is never reset or reused.
        """

        self._check_initialized()
        if retention_days < 30:
            raise ValueError("history retention must be at least 30 days")
        cutoff = _now(now) - retention_days * 86_400
        try:
            with self._write_transaction() as connection:
                connection.execute(
                    "CREATE TEMP TABLE IF NOT EXISTS visible_nzo(nzo_id TEXT PRIMARY KEY)"
                )
                connection.execute("DELETE FROM visible_nzo")
                connection.executemany(
                    "INSERT OR IGNORE INTO visible_nzo(nzo_id) VALUES(?)",
                    ((item,) for item in visible_nzo_ids if item),
                )
                cursor = connection.execute(
                    """
                    DELETE FROM id_map
                    WHERE state = 'history'
                      AND last_seen_at < ?
                      AND NOT EXISTS (
                          SELECT 1 FROM visible_nzo
                          WHERE visible_nzo.nzo_id = id_map.nzo_id
                      )
                    """,
                    (cutoff,),
                )
                removed = cursor.rowcount
        except sqlite3.DatabaseError as exc:
            raise IdMapUnavailable("database cleanup failed") from exc
        return int(removed)

    def healthcheck(self) -> bool:
        if not self._initialized:
            return False
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()
                connection.execute("SELECT 1 FROM id_map LIMIT 1").fetchone()
        except (sqlite3.DatabaseError, OSError):
            return False
        return row is not None and row["value"] == _SCHEMA_VERSION

    def snapshot(self) -> list[dict[str, object]]:
        self._check_initialized()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT nzb_id, nzo_id, created_at, last_seen_at, state "
                    "FROM id_map ORDER BY nzb_id"
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise IdMapUnavailable("database read failed") from exc
        return [dict(row) for row in rows]

    def _check_initialized(self) -> None:
        if not self._initialized:
            raise IdMapUnavailable("database is not initialized")

    @staticmethod
    def _validate_nzo_id(nzo_id: str) -> str:
        if not isinstance(nzo_id, str):
            raise ValueError("nzo_id must be a string")
        if not nzo_id or len(nzo_id) > 512:
            raise ValueError("nzo_id must be between 1 and 512 characters")
        return nzo_id

    @staticmethod
    def _validate_state(state: str) -> None:
        if state not in _VALID_STATES:
            raise ValueError("state must be unknown, queue, or history")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")


def _now(value: int | None) -> int:
    if value is not None:
        return value
    import time

    return int(time.time())
