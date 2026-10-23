"""Unit conversion helpers for SABnzbd → NZBGet fields."""

from __future__ import annotations

import re

MIB = 1024 * 1024
GIB = 1024 * MIB
_U32 = 0xFFFFFFFF
_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?I?B?)?\s*$", re.I)
_UPTIME_RE = re.compile(r"(\d+)\s*([dhms])", re.I)


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def split_u32(value: int) -> tuple[int, int]:
    value = max(0, int(value))
    return value & _U32, (value >> 32) & _U32


def mib_to_bytes(value: object) -> int:
    """SAB ``mb`` fields are MiB strings/floats."""

    return max(0, int(as_float(value, 0.0) * MIB))


def size_fields(prefix: str, byte_count: int) -> dict[str, int]:
    byte_count = max(0, int(byte_count))
    lo, hi = split_u32(byte_count)
    return {
        f"{prefix}Lo": lo,
        f"{prefix}Hi": hi,
        f"{prefix}MB": byte_count // MIB,
    }


def rate_fields(prefix: str, bytes_per_second: int) -> dict[str, int]:
    bytes_per_second = max(0, int(bytes_per_second))
    lo, hi = split_u32(bytes_per_second)
    return {f"{prefix}Lo": lo, f"{prefix}Hi": hi}


_UNIT_MULTIPLIERS = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": MIB,
    "MB": MIB,
    "MIB": MIB,
    "G": GIB,
    "GB": GIB,
    "GIB": GIB,
    "T": 1024 * GIB,
    "TB": 1024 * GIB,
    "TIB": 1024 * GIB,
}


def human_to_bytes(value: object) -> int:
    """Parse SAB display units such as ``1.2 GB`` or ``6 MB``."""

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(float(value)))
    if not isinstance(value, str):
        return 0
    match = _SIZE_RE.match(value)
    if not match:
        return 0
    amount = float(match.group(1))
    unit = (match.group(2) or "").upper()
    return max(0, int(amount * _UNIT_MULTIPLIERS.get(unit, 1)))


def parse_uptime_seconds(value: object) -> int:
    if not isinstance(value, str):
        return 0
    seconds = 0
    for amount, unit in _UPTIME_RE.findall(value.lower()):
        factor = {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        seconds += int(amount) * factor
    return seconds
