"""History translation contract tests.

The expected field set is taken from the NZBGet v21.1 implementation
(``daemon/remote/XmlRpc.cpp``): ``HistoryXmlCommand::Execute`` emits the
``JSON_HISTORY_ITEM_START``/``JSON_HISTORY_ITEM_END`` fields plus the fields
appended by ``NzbInfoXmlCommand::AppendNzbInfoFields``.
"""

from __future__ import annotations

from nzbget_sab_bridge.sab.models import SabHistorySlot
from nzbget_sab_bridge.translate.history import translate_history

# NZBGet v21.1 history item fields.
NZBGET_V21_HISTORY_FIELDS = {
    "Category",
    "CriticalHealth",
    "Deleted",
    "DeleteStatus",
    "DestDir",
    "DownloadedSizeHi",
    "DownloadedSizeLo",
    "DownloadedSizeMB",
    "DownloadTimeSec",
    "DupeKey",
    "DupeMode",
    "DupeScore",
    "ExParStatus",
    "ExtraParBlocks",
    "FailedArticles",
    "FileCount",
    "FileSizeHi",
    "FileSizeLo",
    "FileSizeMB",
    "FinalDir",
    "Health",
    "HistoryTime",
    "ID",
    "Kind",
    "Log",
    "MarkStatus",
    "MaxPostTime",
    "MessageCount",
    "MinPostTime",
    "MoveStatus",
    "Name",
    "NZBFilename",
    "NZBID",
    "NZBName",
    "NZBNicename",
    "Parameters",
    "ParStatus",
    "ParTimeSec",
    "PostTotalTimeSec",
    "RemainingFileCount",
    "RepairTimeSec",
    "RetryData",
    "ScriptStatus",
    "ScriptStatuses",
    "ServerStats",
    "Status",
    "SuccessArticles",
    "TotalArticles",
    "UnpackStatus",
    "UnpackTimeSec",
    "URL",
    "UrlStatus",
}


def _slot(**overrides: object) -> SabHistorySlot:
    payload: dict[str, object] = {
        "nzo_id": "synthetic-nzo",
        "name": "Synthetic.Release",
        "nzb_name": "Synthetic.Release.nzb",
        "category": "tv",
        "status": "Completed",
        "bytes": 1024,
        "downloaded": 1024,
        "completed": 1_700_000_000,
        "time_added": 1_699_999_000,
    }
    payload.update(overrides)
    return SabHistorySlot.model_validate(payload)


def test_history_item_exposes_the_nzbget_v21_field_set() -> None:
    record = translate_history([_slot()], [42])[0]

    assert set(record) == NZBGET_V21_HISTORY_FIELDS


def test_history_item_identifiers_and_status_are_stable() -> None:
    record = translate_history([_slot()], [42])[0]

    assert record["NZBID"] == 42
    assert record["ID"] == 42
    assert record["Kind"] == "NZB"
    assert record["Status"] == "SUCCESS/ALL"
    assert record["HistoryTime"] == 1_700_000_000
    assert record["URL"] == ""
    assert record["RetryData"] is False
    assert record["Deleted"] is False


def test_history_item_sizes_are_split_like_nzbget() -> None:
    # 5 GiB = 5368709120 bytes -> high word 1, low word 1073741824.
    record = translate_history([_slot(bytes=5 * 1024 * 1024 * 1024)], [1])[0]

    assert record["FileSizeHi"] == 1
    assert record["FileSizeLo"] == 1073741824
    assert record["FileSizeMB"] == 5120


def test_history_zero_size_records_are_preserved() -> None:
    record = translate_history(
        [_slot(status="Failed", bytes=0, downloaded=0, fail_message="scan failed")],
        [1],
    )[0]

    assert record["FileSizeLo"] == 0
    assert record["DownloadedSizeLo"] == 0
    assert record["Status"] == "FAILURE/SCAN"
    assert record["DeleteStatus"] == "SCAN"


def test_history_scan_failures_use_the_nzbget_status_and_delete_status() -> None:
    """NZBGet reports a malformed nzb-file as FAILURE/SCAN + DeleteStatus SCAN.

    daemon/queue/DownloadInfo.cpp maps m_deleteStatus == dsScan to
    "FAILURE/SCAN" and its DeleteStatus enum value is "SCAN".
    """

    scan = translate_history(
        [_slot(status="Failed", fail_message="Malformed NZB parse error")],
        [1],
    )[0]
    assert scan["Status"] == "FAILURE/SCAN"
    assert scan["DeleteStatus"] == "SCAN"

    unpack = translate_history(
        [_slot(status="Failed", fail_message="Unpack failed: password")],
        [2],
    )[0]
    assert unpack["Status"] == "FAILURE/UNPACK"
    assert unpack["DeleteStatus"] == "NONE"
