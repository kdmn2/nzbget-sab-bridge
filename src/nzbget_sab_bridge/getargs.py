"""NZBGet GET-form RPC argument parsing.

NZBGet's GET form is ``/jsonrpc/<method>?name=value&name=value``. The v21.1
implementation (``daemon/remote/XmlRpc.cpp``) consumes these values
*positionally* and never reads the parameter names:

* ``XmlCommand::NextParamAsStr()`` (GET) finds the first ``=`` and returns the
  value up to the next ``&``. The name is ignored entirely.
* ``XmlCommand::NextParamAsBool()`` (GET) matches that value against the
  prefixes ``true``/``false``.
* ``HistoryXmlCommand::Execute()`` does ``bool dup = false;
  NextParamAsBool(&dup);`` and ignores the result, so an unparseable value
  degrades to the default instead of failing the request.
* ``ListGroupsXmlCommand::Execute()`` does ``int nrEntries = 0;
  NextParamAsInt(&nrEntries);`` with the same positional, best-effort
  semantics.

The bridge therefore reads the first query value positionally and treats an
unrecognized or unparseable value as the method default, matching NZBGet.
Validating parameter *names* instead is stricter than the contract and made
clients that name their argument differently fail with JSON-RPC error code 2
before any backend call.

Query names and values are never forwarded, used for routing, or logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl


class GetArgumentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    hidden: bool = False
    limit: int | None = None
    start: int | None = None
    search: str | None = None


def _first_query_value(query: str) -> str | None:
    """Return the first query value, ignoring the parameter name.

    Mirrors ``XmlCommand::NextParamAsStr()`` for HTTP GET: the first ``=``
    starts the value, which runs up to the next ``&``. A query without ``=``
    yields no value, exactly as NZBGet does.
    """

    separator = query.find("=")
    if separator < 0:
        return None
    value = query[separator + 1 :]
    end = value.find("&")
    return value if end < 0 else value[:end]


def _named_query_values(query: str) -> dict[str, str]:
    """Return named query values for the arguments the bridge understands.

    Names are matched case-insensitively. Duplicate names keep the last value.
    """

    values: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        values[key.casefold()] = value
    return values


def _parse_nonnegative_int(value: str) -> int | None:
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def _parse_bool_like(value: str) -> bool | None:
    """Match NZBGet's JSON ``NextParamAsBool``: the "true"/"false" prefixes.

    NZBGet compares the raw value with ``strncmp(param, "true", 4)`` and
    ``strncmp(param, "false", 5)``, so "1"/"0" are not booleans in JSON mode.
    The comparison is case-sensitive there; the bridge accepts any casing so
    that a client writing "False" is not rejected.
    """

    lowered = value.casefold()
    if lowered.startswith("true"):
        return True
    if lowered.startswith("false"):
        return False
    return None


def _history_query(query: str) -> list[object]:
    named = _named_query_values(query)

    # The Hidden flag is the FIRST positional value for NZBGet's history
    # command, exactly as XmlCommand::NextParamAsBool() reads it. Parameter
    # names are not consulted, so "?anything=false" means hidden=false.
    first = _first_query_value(query)
    hidden = _parse_bool_like(first) if first is not None else None
    if hidden is None:
        hidden = False

    limit = None
    if "limit" in named:
        limit = _parse_nonnegative_int(named["limit"])
    elif "numberoflogentries" in named:
        limit = _parse_nonnegative_int(named["numberoflogentries"])

    start = None
    if "start" in named:
        start = _parse_nonnegative_int(named["start"])
    elif "offset" in named:
        start = _parse_nonnegative_int(named["offset"])

    search = named.get("search")
    if search is not None:
        search = search.strip()[:200]

    return [HistoryQuery(hidden=hidden, limit=limit, start=start, search=search)]


def parse_get_params(method: str, query: str) -> list[object]:
    if not query:
        return []

    lowered = method.casefold()
    if lowered == "history":
        return _history_query(query)

    if lowered == "listgroups":
        named = _named_query_values(query)
        if "numberoflogentries" in named:
            number = _parse_nonnegative_int(named["numberoflogentries"])
            return [number if number is not None else 0]
        first = _first_query_value(query)
        if first is None:
            return []
        number = _parse_nonnegative_int(first)
        return [number if number is not None else 0]

    if lowered in {"version", "config", "configtemplates", "status"}:
        # These commands read no arguments. NZBGet ignores trailing query
        # values for them, so the bridge does too.
        return []

    raise GetArgumentError("Unsupported GET method")
