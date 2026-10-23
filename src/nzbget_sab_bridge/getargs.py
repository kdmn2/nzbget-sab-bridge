"""NZBGet GET-form RPC argument parsing.

NZBGet's GET form is ``/jsonrpc/<method>?name=value&name=value``. The official
server consumes values positionally and ignores names, but the bridge accepts
only the parameter names belonging to the supported read-only methods so
unsupported semantic arguments cannot be silently dropped.

A small allowlist of conventional cache-busting query names is tolerated. Those
values are never forwarded, used, or logged.
"""

from __future__ import annotations

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


def parse_get_params(method: str, query: str) -> list[object]:
    if not query:
        return []

    pairs = parse_qsl(query, keep_blank_values=True)
    meaningful: list[tuple[str, str]] = []
    for key, value in pairs:
        # Cache busters are harmless only when they are purely numeric or
        # empty. Anything else is treated as an unsupported argument.
        if key.casefold() in _CACHE_BUSTER_KEYS and (value == "" or value.isdigit()):
            continue
        meaningful.append((key, value))

    lowered = method.casefold()
    if lowered == "history":
        if not meaningful:
            return []
        if len(meaningful) != 1:
            raise GetArgumentError("Invalid GET argument count")
        key, value = meaningful[0]
        if key.casefold() != "hidden" or value.casefold() not in {"true", "false"}:
            raise GetArgumentError("Invalid GET argument (Hidden)")
        return [value.casefold() == "true"]

    if lowered == "listgroups":
        if not meaningful:
            return []
        if len(meaningful) != 1:
            raise GetArgumentError("Invalid GET argument count")
        key, value = meaningful[0]
        if key.casefold() != "numberoflogentries":
            raise GetArgumentError("Invalid GET argument")
        try:
            number = int(value)
        except ValueError as exc:
            raise GetArgumentError("Invalid GET argument (NumberOfLogEntries)") from exc
        if number < 0:
            raise GetArgumentError("Invalid GET argument (NumberOfLogEntries)")
        return [number]

    if lowered in {"version", "config", "configtemplates", "status"}:
        if meaningful:
            raise GetArgumentError("Unsupported GET argument")
        return []

    raise GetArgumentError("Unsupported GET method")
