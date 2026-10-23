from __future__ import annotations

import pytest
from scripts.release_policy import should_promote, version_key


def test_version_key_accepts_canonical_tags() -> None:
    assert version_key("v0.1.1") == (0, 1, 1)
    assert version_key("0.1.10") == (0, 1, 10)
    assert version_key("v1.0.0") == (1, 0, 0)
    assert version_key("v0.1") is None
    assert version_key("main") is None


def test_latest_never_moves_backward() -> None:
    assert should_promote("v0.1.1", "v0.1.2") is True
    assert should_promote("v0.1.1", "v0.1.1") is False
    assert should_promote("v0.1.1", "v0.1.0") is False
    assert should_promote("v0.1.9", "v0.1.10") is True
    assert should_promote("v1.0.0", "v0.9.9") is False


def test_missing_or_nonrelease_latest_may_be_replaced() -> None:
    assert should_promote(None, "v0.1.2") is True
    assert should_promote("main", "v0.1.2") is True


def test_invalid_candidate_is_rejected() -> None:
    with pytest.raises(ValueError, match="candidate"):
        should_promote("v0.1.1", "latest")
