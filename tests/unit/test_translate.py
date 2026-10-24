from __future__ import annotations

import json
from pathlib import Path

import pytest

from nzbget_sab_bridge.sab.models import SabQueue
from nzbget_sab_bridge.translate.categories import category_matches, resolve_category
from nzbget_sab_bridge.translate.config import build_config
from nzbget_sab_bridge.translate.queue import translate_queue
from nzbget_sab_bridge.translate.status import (
    history_status_to_nzbget,
    normalize_category,
    priority_from_sab,
    priority_to_sab,
    queue_status_to_nzbget,
    sab_priority_label,
)
from nzbget_sab_bridge.translate.units import (
    human_to_bytes,
    mib_to_bytes,
    parse_uptime_seconds,
    size_fields,
    split_u32,
)


def test_mib_and_u32_conversion() -> None:
    assert mib_to_bytes("1024.00") == 1024 * 1024 * 1024
    assert mib_to_bytes(None) == 0
    assert split_u32(0x1_0000_0001) == (1, 1)
    assert size_fields("FileSize", 2 * 1024 * 1024) == {
        "FileSizeLo": 2 * 1024 * 1024,
        "FileSizeHi": 0,
        "FileSizeMB": 2,
    }


def test_human_size_and_uptime() -> None:
    assert human_to_bytes("100.0 G") == 100 * 1024 * 1024 * 1024
    assert human_to_bytes("6 MB") == 6 * 1024 * 1024
    assert human_to_bytes("0 ") == 0
    assert parse_uptime_seconds("1h 2m") == 3720
    assert parse_uptime_seconds("2d 1h") == 176400
    assert parse_uptime_seconds("") == 0


def test_priority_and_category_mapping() -> None:
    assert priority_from_sab("Low") == -50
    assert priority_from_sab("Normal") == 0
    assert priority_from_sab("High") == 50
    assert priority_from_sab("Force") == 900
    assert priority_from_sab("Paused") == 0
    assert normalize_category("*") == ""
    assert normalize_category("None") == ""
    assert normalize_category(" tv ") == "tv"


@pytest.mark.parametrize(
    ("nzbget_priority", "sab_priority", "label"),
    [
        (-100, -1, "Low"),
        (-50, -1, "Low"),
        (-1, -1, "Low"),
        (0, 0, "Normal"),
        (1, 1, "High"),
        (50, 1, "High"),
        (100, 1, "High"),
        (899, 1, "High"),
        (900, 2, "Force"),
        (1000, 2, "Force"),
    ],
)
def test_lossy_priority_write_mapping(nzbget_priority: int, sab_priority: int, label: str) -> None:
    assert priority_to_sab(nzbget_priority) == sab_priority
    assert sab_priority_label(sab_priority) == label


@pytest.mark.parametrize(
    ("sab_status", "expected"),
    [
        ("Downloading", "DOWNLOADING"),
        ("Paused", "PAUSED"),
        ("Queued", "QUEUED"),
        ("Propagating", "QUEUED"),
        ("Checking", "VERIFYING_SOURCES"),
        ("Fetching", "FETCHING"),
        ("unknown", "QUEUED"),
    ],
)
def test_queue_status_mapping(sab_status: str, expected: str) -> None:
    assert queue_status_to_nzbget(sab_status) == expected


@pytest.mark.parametrize(
    ("sab_status", "message", "expected"),
    [
        ("Completed", "", "SUCCESS/ALL"),
        ("Failed", "Unpack failed: password", "FAILURE/UNPACK"),
        ("Failed", "Repair failed, not enough blocks", "FAILURE/PAR"),
        ("Failed", "Could not move files to destination", "FAILURE/MOVE"),
        ("Failed", "Malformed NZB parse error", "FAILURE/SCAN"),
        ("Failed", "Download aborted, articles missing", "FAILURE/HEALTH"),
        ("Verifying", "", "FAILURE/HEALTH"),
    ],
)
def test_history_status_mapping(sab_status: str, message: str, expected: str) -> None:
    assert history_status_to_nzbget(sab_status, message) == expected


def test_queue_age_uses_sab_avg_age_instead_of_epoch() -> None:
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "sab" / "queue.json").read_text(encoding="utf-8")
    )
    queue = SabQueue.model_validate(payload["queue"])
    group = translate_queue(queue, [1], now=1_700_000_000)[0]
    assert group["MaxPostTime"] == 1_700_000_000 - 2 * 3600
    assert group["MinPostTime"] == group["MaxPostTime"]


def test_minimal_config_categories() -> None:
    assert build_config(["*", "tv", "tv", "movies"]) == [
        {"Name": "Category1.Name", "Value": "Default"},
        {"Name": "Category2.Name", "Value": "tv"},
        {"Name": "Category3.Name", "Value": "movies"},
    ]
    assert build_config([]) == []
    assert build_config(["*", "Default", "tv"]) == [
        {"Name": "Category1.Name", "Value": "*"},
        {"Name": "Category2.Name", "Value": "Default"},
        {"Name": "Category3.Name", "Value": "tv"},
    ]


def test_category_resolution_uses_sab_default_marker() -> None:
    categories = ["*", "tv", "movies"]
    assert resolve_category("", categories) == "*"
    assert resolve_category("*", categories) == "*"
    assert resolve_category("Default", categories) == "*"
    assert resolve_category("tv", categories) == "tv"
    assert resolve_category("TV", categories) == "tv"
    with pytest.raises(ValueError):
        resolve_category("missing", categories)
    with pytest.raises(ValueError):
        resolve_category("Default", ["tv"])


def test_category_matching() -> None:
    assert category_matches("*", "*")
    assert category_matches("None", "*")
    assert category_matches("", "*")
    assert category_matches("tv", "tv")
    assert category_matches("TV", "tv")
    assert not category_matches("movies", "tv")
    assert not category_matches("Default", "*")
