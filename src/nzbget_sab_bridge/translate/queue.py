"""Translate SABnzbd queue slots into NZBGet ``listgroups`` structures."""

from __future__ import annotations

import time

from ..sab.models import SabQueue
from .status import (
    normalize_category,
    priority_from_sab,
    queue_status_to_nzbget,
)
from .units import mib_to_bytes, parse_uptime_seconds, size_fields


def translate_queue(
    queue: SabQueue, nzb_ids: list[int], *, now: int | None = None
) -> list[dict[str, object]]:
    if len(nzb_ids) != len(queue.slots):
        raise ValueError("ID count does not match SAB queue slot count")

    current_time = int(time.time()) if now is None else now
    groups: list[dict[str, object]] = []
    for slot, nzb_id in zip(queue.slots, nzb_ids, strict=True):
        total_bytes = mib_to_bytes(slot.mb)
        remaining_bytes = mib_to_bytes(slot.mbleft)
        downloaded_bytes = max(0, total_bytes - remaining_bytes)
        priority = priority_from_sab(slot.priority)
        status = queue_status_to_nzbget(slot.status)
        paused = status == "PAUSED" or slot.priority.strip().lower() == "paused"
        filename = slot.filename or slot.nzo_id
        age_seconds = parse_uptime_seconds(slot.avg_age)
        post_time = (
            current_time - age_seconds if age_seconds > 0 else (slot.time_added or current_time)
        )

        group: dict[str, object] = {
            "NZBID": nzb_id,
            "FirstID": nzb_id,
            "LastID": nzb_id,
            "NZBFilename": filename,
            "NZBName": filename,
            "NZBNicename": filename,
            "Kind": "NZB",
            "URL": "",
            "DestDir": "",
            "FinalDir": "",
            "Category": normalize_category(slot.cat),
            **size_fields("FileSize", total_bytes),
            **size_fields("RemainingSize", remaining_bytes),
            **size_fields("PausedSize", remaining_bytes if paused else 0),
            "FileCount": 0,
            "RemainingFileCount": 0,
            "RemainingParCount": 0,
            "MinPostTime": post_time,
            "MaxPostTime": post_time,
            "MinPriority": priority,
            "MaxPriority": priority,
            "ActiveDownloads": 1 if status == "DOWNLOADING" else 0,
            "Status": status,
            "TotalArticles": 0,
            "SuccessArticles": 0,
            "FailedArticles": 0,
            "Health": 1000,
            "CriticalHealth": 1000,
            **size_fields("DownloadedSize", downloaded_bytes),
            "DownloadTimeSec": 0,
            "MessageCount": 0,
            "DupeKey": "",
            "DupeScore": 0,
            "DupeMode": "SCORE",
            "Parameters": [],
            "ServerStats": [],
            "Deleted": False,
            "ParStatus": "NONE",
            "UnpackStatus": "NONE",
            "MoveStatus": "NONE",
            "ScriptStatus": "NONE",
            "DeleteStatus": "NONE",
            "UrlStatus": "NONE",
            "MarkStatus": "NONE",
            "ExParStatus": "",
            "ExtraParBlocks": 0,
            "ScriptStatuses": [],
            "PostTotalTimeSec": 0,
            "ParTimeSec": 0,
            "RepairTimeSec": 0,
            "UnpackTimeSec": 0,
            "PostInfoText": "",
            "PostStageProgress": 0,
            "PostStageTimeSec": 0,
            "Log": [],
        }
        groups.append(group)
    return groups
