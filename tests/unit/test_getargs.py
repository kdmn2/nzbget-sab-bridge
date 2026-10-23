from __future__ import annotations

import pytest

from nzbget_sab_bridge.getargs import GetArgumentError, parse_get_params


def test_no_query_means_no_arguments() -> None:
    assert parse_get_params("version", "") == []
    assert parse_get_params("history", "") == []


def test_history_hidden_argument() -> None:
    assert parse_get_params("history", "Hidden=false") == [False]
    assert parse_get_params("history", "hidden=TRUE") == [True]
    assert parse_get_params("history", "Hidden=false&_=123456") == [False]


def test_listgroups_argument() -> None:
    assert parse_get_params("listgroups", "NumberOfLogEntries=0") == [0]
    assert parse_get_params("listgroups", "numberoflogentries=2") == [2]


def test_cache_busters_are_tolerated_only_when_harmless() -> None:
    assert parse_get_params("version", "_=123") == []
    assert parse_get_params("config", "t=1700000000") == []
    assert parse_get_params("configtemplates", "timestamp=1700000000") == []
    with pytest.raises(GetArgumentError):
        parse_get_params("version", "_=not-a-number")


@pytest.mark.parametrize(
    "query",
    [
        "unknown=value",
        "Hidden=true&Hidden=false",
        "Hidden=1",
        "NumberOfLogEntries=abc",
        "NumberOfLogEntries=-1",
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
