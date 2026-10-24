"""Translate SABnzbd history slots into NZBGet ``history`` structures."""

from __future__ import annotations

from ..sab.models import SabHistorySlot
from .status import (
    history_move_status,
    history_par_status,
    history_script_status,
    history_stage_names,
    history_status_to_nzbget,
    history_unpack_status,
    normalize_category,
)
from .units import size_fields


def translate_history(slots: list[SabHistorySlot], nzb_ids: list[int]) -> list[dict[str, object]]:
    if len(nzb_ids) != len(slots):
        raise ValueError("ID count does not match SAB history slot count")

    records: list[dict[str, object]] = []
    for slot, nzb_id in zip(slots, nzb_ids, strict=True):
        name = slot.name or slot.nzb_name
        nzb_filename = slot.nzb_name or (f"{name}.nzb" if name else "")
        stages = history_stage_names([stage.name for stage in slot.stage_log])
        status = history_status_to_nzbget(slot.status, slot.fail_message)
        failed = slot.status.strip().lower() == "failed"
        completed = slot.status.strip().lower() == "completed"
        storage = slot.storage or ""

        record: dict[str, object] = {
            "NZBID": nzb_id,
            "ID": nzb_id,
            "Kind": "NZB",
            "NZBFilename": nzb_filename,
            "Name": name,
            "NZBName": name,
            "NZBNicename": name,
            "URL": "",
            "RetryData": False,
            "HistoryTime": slot.completed or slot.time_added,
            "DestDir": storage,
            "FinalDir": storage,
            "Category": normalize_category(slot.category),
            **size_fields("FileSize", max(0, int(slot.bytes))),
            "FileCount": 0,
            "RemainingFileCount": 0,
            "MinPostTime": 0,
            "MaxPostTime": 0,
            "TotalArticles": 0,
            "SuccessArticles": 0,
            "FailedArticles": 0,
            "Health": 1000,
            "CriticalHealth": 1000,
            "Deleted": False,
            **size_fields("DownloadedSize", max(0, int(slot.downloaded))),
            "DownloadTimeSec": max(0, int(slot.download_time)),
            "PostTotalTimeSec": max(0, int(slot.postproc_time)),
            "ParTimeSec": 0,
            "RepairTimeSec": 0,
            "UnpackTimeSec": 0,
            "MessageCount": 0,
            "DupeKey": "",
            "DupeScore": 0,
            "DupeMode": "SCORE",
            "Parameters": [],
            "ServerStats": [],
            "Log": [],
            "Status": status,
            "ParStatus": history_par_status(slot.status, slot.fail_message, stages),
            "ExParStatus": "",
            "UnpackStatus": history_unpack_status(slot.status, slot.fail_message, stages),
            "UrlStatus": "NONE",
            "ScriptStatus": history_script_status(slot.status, stages),
            "ScriptStatuses": [],
            "MoveStatus": history_move_status(slot.status, slot.fail_message, stages),
            # NZBGet pairs FAILURE/SCAN with DeleteStatus=SCAN for a
            # malformed nzb-file (daemon/queue/DownloadInfo.cpp).
            "DeleteStatus": "SCAN" if status == "FAILURE/SCAN" else "NONE",
            "MarkStatus": "NONE",
            "ExtraParBlocks": 0,
        }
        if not (completed or failed):
            # Callers should filter to final history, but keep the record
            # structurally valid if a post-processing item slips through.
            record["Status"] = "WARNING/DAMAGED"
        records.append(record)
    return records
