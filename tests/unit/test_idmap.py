from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nzbget_sab_bridge.storage.idmap import (
    IdMap,
    IdMapExhausted,
    IdMapUnavailable,
    ensure_data_dir,
)


def _map(tmp_path: Path, *, max_nzb_id: int = 2_147_483_647) -> IdMap:
    instance = IdMap(tmp_path / "idmap.sqlite3", max_nzb_id=max_nzb_id)
    instance.initialize()
    return instance


def test_allocate_and_bidirectional_lookup(tmp_path: Path) -> None:
    mapping = _map(tmp_path)
    nzb_id = mapping.allocate("SABnzbd_nzo_abc", state="queue", now=100)
    assert nzb_id == 1
    assert mapping.get_nzb_id("SABnzbd_nzo_abc") == nzb_id
    assert mapping.get_nzo_id(nzb_id) == "SABnzbd_nzo_abc"
    assert (mapping.path.stat().st_mode & 0o777) == 0o600


def test_restart_persistence(tmp_path: Path) -> None:
    first = _map(tmp_path)
    nzb_id = first.allocate("nzo-one", state="queue")
    second = IdMap(tmp_path / "idmap.sqlite3")
    second.initialize()
    assert second.get_nzb_id("nzo-one") == nzb_id
    assert second.allocate("nzo-two") == nzb_id + 1


def test_queue_to_history_keeps_same_integer(tmp_path: Path) -> None:
    mapping = _map(tmp_path)
    nzb_id = mapping.allocate("nzo-history", state="queue")
    mapping.mark_state("nzo-history", "history", now=200)
    assert mapping.get_nzb_id("nzo-history") == nzb_id
    assert mapping.allocate("nzo-history", state="history") == nzb_id
    row = mapping.snapshot()[0]
    assert row["state"] == "history"


def test_same_nzo_concurrent_first_sighting(tmp_path: Path) -> None:
    mapping = _map(tmp_path)

    with ThreadPoolExecutor(max_workers=12) as executor:
        assigned = list(executor.map(lambda _: mapping.allocate("shared-nzo"), range(24)))
    assert len(set(assigned)) == 1
    assert len(mapping.snapshot()) == 1


def test_distinct_nzos_concurrent_allocation_is_unique(tmp_path: Path) -> None:
    mapping = _map(tmp_path)

    with ThreadPoolExecutor(max_workers=16) as executor:
        assigned = list(executor.map(lambda index: mapping.allocate(f"nzo-{index}"), range(80)))
    assert len(set(assigned)) == 80
    assert all(1 <= value <= 2_147_483_647 for value in assigned)
    assert len(mapping.snapshot()) == 80


def test_locked_database_wait_is_bounded(tmp_path: Path) -> None:
    mapping = IdMap(tmp_path / "idmap.sqlite3", busy_timeout_ms=100)
    mapping.initialize()
    blocker = sqlite3.connect(mapping.path)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()
    try:
        with pytest.raises(IdMapUnavailable, match="write failed"):
            mapping.allocate("blocked")
    finally:
        blocker.rollback()
        blocker.close()
    assert time.monotonic() - started < 2.0


def test_disk_full_error_is_mapped_without_deleting_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping = _map(tmp_path)

    def fail_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr("nzbget_sab_bridge.storage.idmap.sqlite3.connect", fail_connect)
    with pytest.raises(IdMapUnavailable, match="write failed"):
        mapping.allocate("disk-full")
    assert mapping.path.exists()


def test_sqlite_backup_api_restore_preserves_ids(tmp_path: Path) -> None:
    source = _map(tmp_path)
    expected = source.allocate("nzo-backup", state="queue")
    backup_path = tmp_path / "backup.sqlite3"
    source_connection = sqlite3.connect(source.path)
    destination_connection = sqlite3.connect(backup_path)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    restored = IdMap(backup_path)
    restored.initialize()
    assert restored.get_nzb_id("nzo-backup") == expected


def test_31_bit_exhaustion(tmp_path: Path) -> None:
    mapping = _map(tmp_path, max_nzb_id=2)
    assert mapping.allocate("one") == 1
    assert mapping.allocate("two") == 2
    with pytest.raises(IdMapExhausted):
        mapping.allocate("three")
    assert mapping.get_nzb_id("three") is None


def test_malformed_database_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "idmap.sqlite3"
    path.write_bytes(b"this is not sqlite")
    original = path.read_bytes()
    mapping = IdMap(path)
    with pytest.raises(IdMapUnavailable):
        mapping.initialize()
    assert path.read_bytes() == original


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "idmap.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    connection.execute("INSERT INTO meta VALUES('schema_version', 99)")
    connection.commit()
    connection.close()
    mapping = IdMap(path)
    with pytest.raises(IdMapUnavailable, match="schema version"):
        mapping.initialize()


def test_storage_healthcheck(tmp_path: Path) -> None:
    mapping = _map(tmp_path)
    assert mapping.healthcheck() is True
    mapping.path.write_bytes(b"broken")
    assert mapping.healthcheck() is False


def test_prune_history_is_conservative(tmp_path: Path) -> None:
    mapping = _map(tmp_path)
    now = 10_000_000
    queue_id = mapping.allocate("queue-nzo", state="queue", now=100)
    old_history_id = mapping.allocate("old-history", state="history", now=100)
    visible_history_id = mapping.allocate("visible-history", state="history", now=100)
    new_history_id = mapping.allocate("new-history", state="history", now=now)

    removed = mapping.prune_history(
        retention_days=30,
        visible_nzo_ids={"visible-history"},
        now=now,
    )
    assert removed == 1
    assert mapping.get_nzo_id(queue_id) == "queue-nzo"
    assert mapping.get_nzo_id(old_history_id) is None
    assert mapping.get_nzo_id(visible_history_id) == "visible-history"
    assert mapping.get_nzo_id(new_history_id) == "new-history"


def test_prune_requires_minimum_retention(tmp_path: Path) -> None:
    mapping = _map(tmp_path)
    with pytest.raises(ValueError, match="at least 30"):
        mapping.prune_history(retention_days=29, visible_nzo_ids=set())


def test_allocate_many_preserves_order(tmp_path: Path) -> None:
    mapping = _map(tmp_path)
    ids = mapping.allocate_many(["a", "b", "a", "c"])
    assert ids[0] == ids[2]
    assert len(set(ids)) == 3
    assert mapping.get_nzo_id(ids[0]) == "a"


def test_invalid_state_and_id_are_rejected(tmp_path: Path) -> None:
    mapping = _map(tmp_path)
    with pytest.raises(ValueError, match="state"):
        mapping.allocate("x", state="bogus")
    with pytest.raises(ValueError, match="nzo_id"):
        mapping.allocate("")


def test_data_directory_probe_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_open(*_args: object, **_kwargs: object) -> int:
        raise OSError("denied")

    monkeypatch.setattr(os, "open", fail_open)
    with pytest.raises(IdMapUnavailable, match="not writable"):
        ensure_data_dir(tmp_path / "data")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to a read-only directory")
def test_unwritable_data_directory(tmp_path: Path) -> None:
    path = tmp_path / "data"
    path.mkdir()
    path.chmod(0o500)
    with pytest.raises(IdMapUnavailable):
        ensure_data_dir(path)
