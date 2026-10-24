from __future__ import annotations

import pytest

from nzbget_sab_bridge.getargs import GetArgumentError, HistoryQuery, parse_get_params


def test_no_query_means_no_arguments() -> None:
    assert parse_get_params("version", "") == []
    assert parse_get_params("history", "") == []


def test_history_hidden_argument() -> None:
    assert parse_get_params("history", "Hidden=false") == [HistoryQuery(hidden=False)]
    assert parse_get_params("history", "hidden=TRUE") == [HistoryQuery(hidden=True)]
    assert parse_get_params("history", "Hidden=0") == [HistoryQuery(hidden=False)]
    assert parse_get_params("history", "Hidden=1") == [HistoryQuery(hidden=True)]
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
        "unknown=value",
        "Hidden=maybe",
        "Hidden=true&Hidden=false",
        "limit=abc",
        "limit=-1",
        "start=-1",
        "NumberOfLogEntries=abc",
        "NumberOfLogEntries=-1",
        "sort=date",
    ],
)
def test_unsupported_or_malformed_arguments_are_errors(query: str) -> None:
    method = "listgroups" if "NumberOfLogEntries" in query else "history"
    with pytest.raises(GetArgumentError):
        parse_get_params(method, query)


def test_no_argument_methods_reject_semantic_arguments() -> None:
    with pytest.raises(GetArgumentError):
        parse_get_params("config", "section=misc")
    with pytest.raises(GetArgumentError):
        parse_get_params("status", "foo=1")
