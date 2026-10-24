"""NZBGet GET-form RPC argument parsing.

NZBGet's GET form is ``/jsonrpc/<method>?name=value&name=value``. The official
server consumes values positionally and ignores names, but the bridge accepts
only the parameter names belonging to the supported read-only methods so
unsupported semantic arguments cannot be silently dropped.

A small allowlist of conventional cache-busting query names is tolerated. Those
values are never forwarded, used, or logged. Cache-buster values are opaque;
only their names are recognized.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl

_CACHE_BUSTER_KEYS = frozenset(
    {
        "_",
        "t",
        "timestamp",
        "cachebuster",
        "cache_bust",
        "cache-bust",
    }
)


class GetArgumentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    hidden: bool = False
    limit: int | None = None
    start: int | None = None
    search: str | None = None


def _parse_nonnegative_int(value: str, label: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise GetArgumentError(f"Invalid GET argument ({label})") from exc
    if number < 0:
        raise GetArgumentError(f"Invalid GET argument ({label})")
    return number


def _parse_bool(value: str, label: str) -> bool:
    lowered = value.casefold()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    raise GetArgumentError(f"Invalid GET argument ({label})")


def _history_query(meaningful: list[tuple[str, str]]) -> list[object]:
    if not meaningful:
        return []

    values: dict[str, str] = {}
    for key, value in meaningful:
        lowered = key.casefold()
        if lowered == "numberoflogentries":
            lowered = "limit"
        elif lowered == "offset":
            lowered = "start"
        if lowered not in {"hidden", "limit", "start", "search"}:
            raise GetArgumentError("Unsupported GET argument")
        if lowered in values:
            raise GetArgumentError("Invalid GET argument count")
        values[lowered] = value

    hidden = _parse_bool(values["hidden"], "Hidden") if "hidden" in values else False
    limit = _parse_nonnegative_int(values["limit"], "Limit") if "limit" in values else None
    start = _parse_nonnegative_int(values["start"], "Start") if "start" in values else None
    search = values.get("search")
    if search is not None:
        search = search.strip()
        if not search:
            search = None
        elif len(search) > 200:
            raise GetArgumentError("Invalid GET argument (Search)")
    return [HistoryQuery(hidden=hidden, limit=limit, start=start, search=search)]


def parse_get_params(method: str, query: str) -> list[object]:
    if not query:
        return []

    pairs = parse_qsl(query, keep_blank_values=True)
    meaningful: list[tuple[str, str]] = []
    for key, value in pairs:
        if key.casefold() in _CACHE_BUSTER_KEYS:
            continue
        meaningful.append((key, value))

    lowered = method.casefold()
    if lowered == "history":
        return _history_query(meaningful)

    if lowered == "listgroups":
        if not meaningful:
            return []
        if len(meaningful) != 1:
            raise GetArgumentError("Invalid GET argument count")
        key, value = meaningful[0]
        if key.casefold() != "numberoflogentries":
            raise GetArgumentError("Invalid GET argument")
        return [_parse_nonnegative_int(value, "NumberOfLogEntries")]

    if lowered in {"version", "config", "configtemplates", "status"}:
        if meaningful:
            raise GetArgumentError("Unsupported GET argument")
        return []

    raise GetArgumentError("Unsupported GET method")
