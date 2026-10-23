"""Bounded, atomic, shape-only capture fixture writer."""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_FILE_RE = re.compile(r"^(?P<ts>\d{13})-(?P<seq>\d{6})-(?P<transport>[a-z0-9_]+)\.json$")
_TRANSPORT_RE = re.compile(r"[^a-z0-9_]+")
_SCHEMA_VERSION = 1


class CaptureError(RuntimeError):
    """Base class for safe capture failures."""


class CaptureWriteError(CaptureError):
    """A fixture could not be written. The backend request is never replayed."""


class CaptureLimitError(CaptureWriteError):
    """The configured fixture retention/count/size limit was reached."""


class CaptureRecorder:
    def __init__(
        self,
        directory: Path,
        *,
        max_records: int = 500,
        max_total_bytes: int = 10 * 1024 * 1024,
        max_record_bytes: int = 64 * 1024,
        retention_days: int = 0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.directory = directory
        self.max_records = max_records
        self.max_total_bytes = max_total_bytes
        self.max_record_bytes = max_record_bytes
        self.retention_days = retention_days
        self._clock = clock
        self._lock = threading.Lock()
        self._sequence = 0
        self._count = 0
        self._bytes = 0
        self._ready = False

    def ensure_ready(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise CaptureWriteError("capture directory could not be created") from exc
        if not self.directory.is_dir():
            raise CaptureWriteError("capture directory path is not a directory")
        self._probe_writable()
        with self._lock:
            self._prune_retention_locked()
            self._scan_locked()
            self._ready = True

    def check_ready(self) -> bool:
        try:
            if not self.directory.is_dir():
                return False
            self._probe_writable()
        except (OSError, CaptureError):
            return False
        return True

    def next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def write(self, record: dict[str, Any], *, sequence: int | None = None) -> Path:
        with self._lock:
            if not self._ready:
                raise CaptureWriteError("capture recorder is not ready")
            seq = self._sequence + 1 if sequence is None else sequence
            self._sequence = max(self._sequence, seq)
            if self._count >= self.max_records:
                raise CaptureLimitError("capture record count limit reached")
            if seq < 1:
                raise CaptureWriteError("capture sequence is invalid")

            payload: dict[str, Any] = dict(record)
            payload["schema_version"] = _SCHEMA_VERSION
            payload["sequence"] = seq
            payload.setdefault("captured_at", self._timestamp())
            serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            encoded = (serialized + "\n").encode("utf-8")
            if len(encoded) > self.max_record_bytes:
                raise CaptureLimitError("capture record size limit exceeded")
            if self._bytes + len(encoded) > self.max_total_bytes:
                raise CaptureLimitError("capture total byte limit reached")

            transport = (
                _TRANSPORT_RE.sub("_", str(payload.get("transport", "unknown")).lower())[:32]
                or "unknown"
            )
            filename = f"{self._clock_ms():013d}-{seq:06d}-{transport}.json"
            final_path = self.directory / filename
            temp_path = self.directory / f".{filename}.tmp-{uuid.uuid4().hex}"
            try:
                descriptor = os.open(
                    temp_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_path, final_path)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        temp_path.unlink()
            except OSError as exc:
                raise CaptureWriteError("capture fixture could not be written") from exc

            self._count += 1
            self._bytes += len(encoded)
            return final_path

    def _probe_writable(self) -> None:
        probe = self.directory / f".capture-probe-{uuid.uuid4().hex}"
        try:
            descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            raise CaptureWriteError("capture directory is not writable") from exc
        else:
            os.close(descriptor)
        finally:
            try:
                probe.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _scan_locked(self) -> None:
        count = 0
        total = 0
        try:
            entries = list(self.directory.iterdir())
        except OSError as exc:
            raise CaptureWriteError("capture directory could not be read") from exc
        for entry in entries:
            try:
                match = _FILE_RE.fullmatch(entry.name)
                if entry.is_file() and match:
                    count += 1
                    total += entry.stat().st_size
                    sequence = int(match.group("seq"))
                    self._sequence = max(self._sequence, sequence)
            except OSError:
                continue
        self._count = count
        self._bytes = total

    def _prune_retention_locked(self) -> None:
        if self.retention_days <= 0:
            return
        cutoff = self._clock() - self.retention_days * 86_400
        try:
            entries = list(self.directory.iterdir())
        except OSError as exc:
            raise CaptureWriteError("capture directory could not be read") from exc
        for entry in entries:
            try:
                if (
                    entry.is_file()
                    and _FILE_RE.fullmatch(entry.name)
                    and entry.stat().st_mtime < cutoff
                ):
                    entry.unlink()
            except OSError:
                continue

    def _timestamp(self) -> str:
        return datetime.fromtimestamp(self._clock(), tz=UTC).isoformat()

    def _clock_ms(self) -> int:
        return int(self._clock() * 1000)
