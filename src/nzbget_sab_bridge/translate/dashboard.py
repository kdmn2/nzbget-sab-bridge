"""Translate SABnzbd queue/status data into NZBGet ``status`` fields."""

from __future__ import annotations

import time

from ..sab.models import SabQueue, SabServerStats, SabStatus
from .units import (
    GIB,
    as_float,
    as_int,
    human_to_bytes,
    mib_to_bytes,
    parse_uptime_seconds,
    rate_fields,
    size_fields,
)


def translate_status(
    queue: SabQueue,
    status: SabStatus,
    server_stats: SabServerStats,
    *,
    now: int | None = None,
) -> dict[str, object]:
    remaining_bytes = mib_to_bytes(queue.mbleft)
    forced_bytes = sum(
        mib_to_bytes(slot.mbleft)
        for slot in queue.slots
        if slot.priority.strip().lower() == "force"
    )
    download_rate = int(as_float(queue.kbpersec, 0.0) * 1024)
    download_limit = max(0, as_int(status.speedlimit_abs))

    free_disk = human_to_bytes(status.diskspace2_norm)
    if free_disk == 0:
        free_disk = int(as_float(status.diskspace2) * GIB)
    total_disk = int(as_float(status.diskspacetotal2) * GIB)

    article_cache = human_to_bytes(status.cache_size)
    paused = bool(status.paused or queue.paused)
    server_standby = paused or not queue.slots
    news_servers = [
        {"ID": index, "Active": bool(server.serveractive)}
        for index, server in enumerate(status.servers, start=1)
    ]
    thread_count = sum(max(0, server.servertotalconn) for server in status.servers)
    quota_reached = bool(status.have_quota and human_to_bytes(status.left_quota) == 0)

    fields: dict[str, object] = {
        **size_fields("RemainingSize", remaining_bytes),
        **size_fields("ForcedSize", forced_bytes),
        **size_fields("DownloadedSize", 0),
        **size_fields("MonthSize", max(0, int(server_stats.month))),
        **size_fields("DaySize", max(0, int(server_stats.day))),
        "QuotaReached": quota_reached,
        **size_fields("ArticleCache", article_cache),
        "DownloadRate": download_rate,
        **rate_fields("DownloadRate", download_rate),
        "AverageDownloadRate": 0,
        **rate_fields("AverageDownloadRate", 0),
        "DownloadLimit": download_limit,
        "ThreadCount": thread_count,
        "PostJobCount": 0,
        "ParJobCount": 0,
        "UrlCount": 0,
        "UpTimeSec": parse_uptime_seconds(status.uptime),
        "DownloadTimeSec": 0,
        "ServerStandBy": server_standby,
        "DownloadPaused": paused,
        "Download2Paused": paused,
        "ServerPaused": paused,
        "PostPaused": False,
        "ScanPaused": False,
        "ServerTime": int(time.time()) if now is None else now,
        "ResumeTime": 0,
        "FeedActive": False,
        **size_fields("FreeDiskSpace", free_disk),
        **size_fields("TotalDiskSpace", total_disk),
        **size_fields("FreeInterDiskSpace", 0),
        **size_fields("TotalInterDiskSpace", 0),
        "QueueScriptCount": 0,
        "NewsServers": news_servers,
    }
    return fields
