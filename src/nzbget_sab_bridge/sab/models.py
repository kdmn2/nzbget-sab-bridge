"""Tolerant, typed models for the SABnzbd JSON API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _none_to_list(value: Any) -> Any:
    return [] if value is None else value


class _TolerantModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SabQueueSlot(_TolerantModel):
    nzo_id: str
    filename: str = ""
    status: str = ""
    priority: str = ""
    cat: str = ""
    mb: str = "0"
    mbleft: str = "0"
    mbmissing: str = "0"
    percentage: str = "0"
    timeleft: str = "0:00:00"
    labels: list[str] = Field(default_factory=list)
    unpackopts: str = ""
    script: str = ""
    index: int = 0
    password: str = ""
    time_added: int = 0
    avg_age: str = ""
    direct_unpack: str | None = None

    _labels_none = field_validator("labels", mode="before")(_none_to_list)


class SabQueue(_TolerantModel):
    status: str = ""
    speed: str = ""
    kbpersec: str = "0"
    paused: bool = False
    slots: list[SabQueueSlot] = Field(default_factory=list)
    noofslots: int = 0
    noofslots_total: int = 0
    start: int = 0
    limit: int = 0
    finish: int = 0
    timeleft: str = "0:00:00"
    mb: str = "0"
    mbleft: str = "0"
    size: str = ""
    sizeleft: str = ""
    speedlimit: str = ""
    speedlimit_abs: str = ""
    version: str = ""
    pause_int: str = ""
    diskspace1: str = ""
    diskspace2: str = ""
    diskspacetotal1: str = ""
    diskspacetotal2: str = ""
    diskspace1_norm: str = ""
    diskspace2_norm: str = ""
    have_warnings: str = "0"
    left_quota: str = ""
    quota: str = ""
    have_quota: bool = False
    finishaction: str | None = None
    paused_all: bool = False

    _slots_none = field_validator("slots", mode="before")(_none_to_list)


class SabStageLog(_TolerantModel):
    name: str = ""
    actions: list[str] = Field(default_factory=list)

    _actions_none = field_validator("actions", mode="before")(_none_to_list)


class SabHistorySlot(_TolerantModel):
    nzo_id: str
    name: str = ""
    nzb_name: str = ""
    category: str = ""
    status: str = ""
    size: str = ""
    bytes: int = 0
    downloaded: int = 0
    completed: int = 0
    time_added: int = 0
    fail_message: str = ""
    retry: int | bool = 0
    storage: str = ""
    path: str = ""
    url: str = ""
    url_info: str = ""
    password: str = ""
    md5sum: str = ""
    postproc_time: int = 0
    download_time: int = 0
    script: str = ""
    script_line: str = ""
    report: str = ""
    action_line: str = ""
    duplicate_key: str = ""
    archive: bool = False
    loaded: bool = False
    completeness: Any = None
    meta: Any = None
    series: str = ""
    stage_log: list[SabStageLog] = Field(default_factory=list)

    _stage_log_none = field_validator("stage_log", mode="before")(_none_to_list)

    @field_validator(
        "name",
        "nzb_name",
        "category",
        "status",
        "size",
        "fail_message",
        "storage",
        "path",
        "url",
        "url_info",
        "password",
        "md5sum",
        "script",
        "script_line",
        "report",
        "action_line",
        "duplicate_key",
        "series",
        mode="before",
    )
    @classmethod
    def _none_to_empty_string(cls, value: Any) -> Any:
        return "" if value is None else value


class SabHistory(_TolerantModel):
    slots: list[SabHistorySlot] = Field(default_factory=list)
    noofslots: int = 0
    ppslots: int = 0
    total_size: str = ""
    month_size: str = ""
    week_size: str = ""
    day_size: str = ""
    last_history_update: int = 0
    version: str = ""

    _slots_none = field_validator("slots", mode="before")(_none_to_list)


class SabServerConnection(_TolerantModel):
    thrdnum: int | None = None
    art_name: str | None = None
    nzf_name: str | None = None
    nzo_name: str | None = None


class SabServerStatus(_TolerantModel):
    servername: str = ""
    serveractive: bool = False
    serveractiveconn: int = 0
    servertotalconn: int = 0
    serverssl: int = 0
    servertotal: int = 0
    servererror: str = ""
    serverwarning: str = ""
    serverpriority: int = 0
    serveroptional: int = 0
    serverbps: str = ""
    serverconnections: list[SabServerConnection] = Field(default_factory=list)

    _connections_none = field_validator("serverconnections", mode="before")(_none_to_list)


class SabStatus(_TolerantModel):
    version: str = ""
    paused: bool = False
    pause_int: str = ""
    paused_all: bool = False
    speedlimit: str = ""
    speedlimit_abs: str = ""
    speed: str = ""
    kbpersec: str = "0"
    uptime: str = ""
    have_warnings: str = "0"
    warnings: list[Any] = Field(default_factory=list)
    diskspace1: str = ""
    diskspace2: str = ""
    diskspacetotal1: str = ""
    diskspacetotal2: str = ""
    diskspace1_norm: str = ""
    diskspace2_norm: str = ""
    downloaddir: str = ""
    completedir: str = ""
    configfn: str = ""
    logfile: str = ""
    cache_art: str = ""
    cache_size: str = ""
    quota: str = ""
    left_quota: str = ""
    have_quota: bool = False
    finishaction: str | None = None
    servers: list[SabServerStatus] = Field(default_factory=list)

    _warnings_none = field_validator("warnings", mode="before")(_none_to_list)
    _servers_none = field_validator("servers", mode="before")(_none_to_list)


class SabVersion(_TolerantModel):
    version: str


class SabCategories(_TolerantModel):
    categories: list[str] = Field(default_factory=list)

    _categories_none = field_validator("categories", mode="before")(_none_to_list)


class SabServerStats(_TolerantModel):
    day: int = 0
    week: int = 0
    month: int = 0
    total: int = 0
    servers: dict[str, Any] = Field(default_factory=dict)


class SabBasicResult(_TolerantModel):
    status: bool
    error: str | None = None


class SabAddResult(_TolerantModel):
    status: bool
    nzo_ids: list[str] = Field(default_factory=list)
    nzo_id: str | None = None
    error: str | None = None

    _nzo_ids_none = field_validator("nzo_ids", mode="before")(_none_to_list)


class SabPriorityResult(_TolerantModel):
    position: int


class SabSwitchResult(_TolerantModel):
    result: dict[str, Any] = Field(default_factory=dict)
