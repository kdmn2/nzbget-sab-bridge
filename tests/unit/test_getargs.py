from __future__ import annotations

import pytest

from nzbget_sab_bridge.getargs import GetArgumentError, HistoryQuery, parse_get_params


def test_no_query_means_no_arguments() -> None:
    assert parse_get_params("version", "") == []
    assert parse_get_params("history", "") == []


def test_history_hidden_argument() -> None:
    assert parse_get_params("history", "Hidden=false") == [HistoryQuery(hidden=False)]
    assert parse_get_params("history", "hidden=TRUE") == [HistoryQuery(hidden=True)]
    # NZBGet's JSON NextParamAsBool only matches the "true"/"false" prefixes,
    # so "0"/"1" are not booleans and the default (false) is kept.
    assert parse_get_params("history", "Hidden=0") == [HistoryQuery(hidden=False)]
    assert parse_get_params("history", "Hidden=1") == [HistoryQuery(hidden=False)]
    assert parse_get_params("history", "Hidden=false&_=123456") == [HistoryQuery(hidden=False)]


def test_history_pagination_and_search_arguments() -> None:
    assert parse_get_params("history", "Hidden=false&limit=25&start=50&_=opaque-cache-buster") == [
        HistoryQuery(hidden=False, limit=25, start=50)
    ]
    assert parse_get_params("history", "limit=0&offset=10") == [
        HistoryQuery(hidden=False, limit=0, start=10)
    ]
    assert parse_get_params("history", "Search=%20synthetic%20") == [
        HistoryQuery(hidden=False, search="synthetic")
    ]


def test_listgroups_argument() -> None:
    assert parse_get_params("listgroups", "NumberOfLogEntries=0") == [0]
    assert parse_get_params("listgroups", "numberoflogentries=2") == [2]


def test_cache_busters_are_tolerated_with_opaque_values() -> None:
    assert parse_get_params("version", "_=123") == []
    assert parse_get_params("config", "t=1700000000") == []
    assert parse_get_params("configtemplates", "timestamp=1700000000") == []
    assert parse_get_params("version", "_=not-a-number") == []
    assert parse_get_params("history", "Hidden=false&_=opaque-uuid") == [HistoryQuery(hidden=False)]


@pytest.mark.parametrize(
    "query",
    [
        "Hidden=true&Hidden=false",
    ],
)
def test_history_reads_only_the_first_value_like_nzbget(query: str) -> None:
    """NZBGet reads one positional value; later values are not consulted."""

    parsed = parse_get_params("history", query)
    assert parsed == [HistoryQuery(hidden=True)]


def test_listgroups_uses_atoi_semantics_like_nzbget() -> None:
    """NZBGet's NextParamAsInt uses atoi(), so a non-numeric value is 0."""

    assert parse_get_params("listgroups", "NumberOfLogEntries=abc") == [0]
    assert parse_get_params("listgroups", "NumberOfLogEntries=-1") == [0]


def test_methods_without_arguments_ignore_query_values_like_nzbget() -> None:
    """NZBGet's config/status commands read no parameters at all."""

    assert parse_get_params("config", "section=misc") == []
    assert parse_get_params("status", "foo=1") == []
    assert parse_get_params("version", "foo=1") == []


# NZBGet v21.1 GET contract (daemon/remote/XmlRpc.cpp):
#   XmlCommand::NextParamAsStr() reads the first '=' and returns the value up
#   to '&'. The parameter NAME is never read.
#   HistoryXmlCommand::Execute() does `bool dup=false; NextParamAsBool(&dup);`
#   and ignores the result, so an unparseable value degrades to the default
#   instead of failing the request.
# The bridge must therefore consume GET values positionally and ignore names,
# otherwise a client that names its argument differently gets RPC error 2.


@pytest.mark.parametrize(
    "query",
    [
        "nocache=1",
        "history=false",
        "unknown=value",
        "sort=date",
        "_=opaque-cache-buster",
        "cache=false",
        "refresh=1",
    ],
)
def test_history_ignores_get_argument_names_like_nzbget(query: str) -> None:
    """Regression: an unrecognized argument NAME must not fail the request."""

    parsed = parse_get_params("history", query)
    assert isinstance(parsed[0], HistoryQuery)
    assert parsed[0].hidden is False


@pytest.mark.parametrize(
    "query",
    [
        "Hidden=false",
        "hidden=false",
        "nocache=false",
        "anything=false",
        "Hidden=FALSE",
    ],
)
def test_history_reads_the_first_get_value_positionally(query: str) -> None:
    parsed = parse_get_params("history", query)
    assert isinstance(parsed[0], HistoryQuery)
    assert parsed[0].hidden is False


@pytest.mark.parametrize(
    "query",
    [
        "Hidden=true",
        "hidden=true",
        "nocache=true",
        "anything=true",
        "Hidden=TRUE",
    ],
)
def test_history_reads_true_values_positionally(query: str) -> None:
    parsed = parse_get_params("history", query)
    assert isinstance(parsed[0], HistoryQuery)
    assert parsed[0].hidden is True


@pytest.mark.parametrize(
    "query",
    [
        "Hidden=maybe",
        "Hidden=1",
        "Hidden=0",
        "nocache=xyz",
    ],
)
def test_history_unparseable_values_degrade_to_the_default(query: str) -> None:
    """NZBGet ignores a failed NextParamAsBool and keeps dup=false."""

    parsed = parse_get_params("history", query)
    assert isinstance(parsed[0], HistoryQuery)
    assert parsed[0].hidden is False


def test_history_named_hidden_is_read_positionally_like_nzbget() -> None:
    """NZBGet ignores the name and reads the first value, so 'nocache=1' wins."""

    parsed = parse_get_params("history", "nocache=1&Hidden=true")
    assert parsed == [HistoryQuery(hidden=False)]


def test_history_ignores_extra_get_values_like_nzbget() -> None:
    """NZBGet reads only the first value for history; the rest are ignored."""

    parsed = parse_get_params("history", "Hidden=false&limit=25&start=50&extra=1")
    assert parsed == [HistoryQuery(hidden=False, limit=25, start=50)]


def test_history_without_an_equals_sign_is_ignored_like_nzbget() -> None:
    """NZBGet's NextParamAsStr fails without '=' and the default is kept."""

    parsed = parse_get_params("history", "Hidden")
    assert parsed == [HistoryQuery(hidden=False)]


def test_unsupported_get_method_is_an_error() -> None:
    with pytest.raises(GetArgumentError):
        parse_get_params("nosuchmethod", "foo=1")
