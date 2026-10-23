from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from nzbget_sab_bridge.capture.recorder import (
    CaptureLimitError,
    CaptureRecorder,
    CaptureWriteError,
)
from tests.conftest import FAKE_BRIDGE_PASSWORD, FAKE_SAB_API_KEY


def _recorder(directory: Path, **kwargs: object) -> CaptureRecorder:
    defaults: dict[str, object] = {
        "max_records": 10,
        "max_total_bytes": 100_000,
        "max_record_bytes": 10_000,
        "clock": lambda: 1_700_000_000.0,
    }
    defaults.update(kwargs)
    recorder = CaptureRecorder(directory, **defaults)  # type: ignore[arg-type]
    recorder.ensure_ready()
    return recorder


def _record() -> dict[str, object]:
    return {
        "transport": "jsonrpc",
        "http_method": "POST",
        "path": "/jsonrpc",
        "request": {"method": "version"},
        "response": {"parse": "ok"},
        "status": 200,
        "duration_ms": 3,
        "error": None,
    }


def test_write_fixture_and_permissions(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path / "captures")
    sequence = recorder.next_sequence()
    path = recorder.write(_record(), sequence=sequence)
    assert path.exists()
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["sequence"] == 1
    assert payload["schema_version"] == 1
    assert payload["transport"] == "jsonrpc"
    assert not list(path.parent.glob(".*.tmp-*"))


def test_fixture_does_not_contain_secret_values(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path / "captures")
    path = recorder.write(_record(), sequence=recorder.next_sequence())
    content = path.read_text(encoding="utf-8")
    assert FAKE_BRIDGE_PASSWORD not in content
    assert FAKE_SAB_API_KEY not in content


def test_sequence_and_filename_order(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path / "captures")
    first = recorder.write(_record(), sequence=recorder.next_sequence())
    second = recorder.write(_record(), sequence=recorder.next_sequence())
    assert first.name < second.name
    assert json.loads(first.read_text())["sequence"] == 1
    assert json.loads(second.read_text())["sequence"] == 2


def test_restart_resumes_sequence_from_existing_files(tmp_path: Path) -> None:
    directory = tmp_path / "captures"
    first = _recorder(directory)
    first.write(_record(), sequence=first.next_sequence())
    restarted = _recorder(directory)
    sequence = restarted.next_sequence()
    assert sequence == 2


def test_max_record_count_stops_recording(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path / "captures", max_records=1)
    recorder.write(_record(), sequence=recorder.next_sequence())
    with pytest.raises(CaptureLimitError):
        recorder.write(_record(), sequence=recorder.next_sequence())
    assert len(list((tmp_path / "captures").glob("*.json"))) == 1


def test_max_total_bytes_stops_recording(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path / "captures", max_total_bytes=20)
    with pytest.raises(CaptureLimitError):
        recorder.write(_record(), sequence=recorder.next_sequence())


def test_max_record_bytes_stops_recording(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path / "captures", max_record_bytes=20)
    with pytest.raises(CaptureLimitError):
        recorder.write(_record(), sequence=recorder.next_sequence())


def test_write_failure_does_not_leave_partial_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _recorder(tmp_path / "captures")

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(CaptureWriteError):
        recorder.write(_record(), sequence=recorder.next_sequence())
    assert list((tmp_path / "captures").glob("*.json")) == []
    assert list((tmp_path / "captures").glob(".*.tmp-*")) == []


def test_retention_prunes_only_matching_old_files(tmp_path: Path) -> None:
    directory = tmp_path / "captures"
    directory.mkdir()
    old = directory / "1000000000000-000001-jsonrpc.json"
    old.write_text("{}\n", encoding="utf-8")
    unrelated = directory / "keep-me.json"
    unrelated.write_text("{}\n", encoding="utf-8")
    old_time = time.time() - 40 * 86_400
    os.utime(old, (old_time, old_time))

    _recorder(directory, retention_days=30, clock=time.time)
    assert not old.exists()
    assert unrelated.exists()


def test_check_ready_false_for_file_path(tmp_path: Path) -> None:
    path = tmp_path / "not-a-directory"
    path.write_text("x", encoding="utf-8")
    recorder = CaptureRecorder(path)
    assert recorder.check_ready() is False
    with pytest.raises(CaptureWriteError):
        recorder.ensure_ready()
