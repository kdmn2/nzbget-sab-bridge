"""SABnzbd status, priority, and category mapping."""

from __future__ import annotations


def normalize_category(value: object) -> str:
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    if not stripped or stripped.lower() in {"*", "none"}:
        return ""
    return stripped


def priority_from_sab(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        numeric = int(value)
        return {-100: 0, -4: -100, -2: 0, -1: -50, 0: 0, 1: 50, 2: 900}.get(numeric, 0)
    if not isinstance(value, str):
        return 0
    return {
        "low": -50,
        "normal": 0,
        "high": 50,
        "force": 900,
        "paused": 0,
        "repair": 0,
    }.get(value.strip().lower(), 0)


def priority_to_sab(value: int) -> int:
    """Map NZBGet's wider priority scale onto SAB's four levels.

    Lossy by design: all negative NZBGet priorities become SAB Low; positive
    priorities below Force become High; Force is reserved for >=900. Very high
    (100) is not promoted to Force.
    """

    if value <= -1:
        return -1
    if value == 0:
        return 0
    if value < 900:
        return 1
    return 2


def sab_priority_label(value: int) -> str:
    return {2: "Force", 1: "High", 0: "Normal", -1: "Low"}.get(value, "Normal")


def queue_status_to_nzbget(value: object) -> str:
    if not isinstance(value, str):
        return "QUEUED"
    return {
        "downloading": "DOWNLOADING",
        "paused": "PAUSED",
        "queued": "QUEUED",
        "propagating": "QUEUED",
        "checking": "VERIFYING_SOURCES",
        "fetching": "FETCHING",
        "grabbing": "FETCHING",
        "deleted": "QUEUED",
        "idle": "QUEUED",
    }.get(value.strip().lower(), "QUEUED")


def history_status_to_nzbget(value: object, fail_message: object) -> str:
    status = value.strip().lower() if isinstance(value, str) else ""
    if status == "completed":
        return "SUCCESS/ALL"
    if status != "failed":
        return "FAILURE/HEALTH"

    message = fail_message.lower() if isinstance(fail_message, str) else ""
    if any(token in message for token in ("unpack", "unrar", "rar", "password")):
        return "FAILURE/UNPACK"
    if any(token in message for token in ("repair", "par2", "par-check", "par ")):
        return "FAILURE/PAR"
    if any(token in message for token in ("move", "storage", "destination")):
        return "FAILURE/MOVE"
    # NZBClient's history renderer is reliable for the generic health failure.
    # In particular, do not relabel scan/parse failures as unpack failures or
    # drop the record; the detailed stage remains available in the source data.
    return "FAILURE/HEALTH"


def history_par_status(value: object, fail_message: object, stages: set[str]) -> str:
    failed = isinstance(value, str) and value.lower() == "failed"
    message = fail_message.lower() if isinstance(fail_message, str) else ""
    involved = "repair" in stages or any(
        token in message for token in ("repair", "par2", "par-check", "par ")
    )
    if not involved:
        return "NONE"
    return "FAILURE" if failed else "SUCCESS"


def history_unpack_status(value: object, fail_message: object, stages: set[str]) -> str:
    failed = isinstance(value, str) and value.lower() == "failed"
    message = fail_message.lower() if isinstance(fail_message, str) else ""
    involved = "unpack" in stages or any(
        token in message for token in ("unpack", "unrar", "rar", "password")
    )
    if not involved:
        return "NONE"
    return "FAILURE" if failed else "SUCCESS"


def history_move_status(value: object, fail_message: object, stages: set[str]) -> str:
    failed = isinstance(value, str) and value.lower() == "failed"
    message = fail_message.lower() if isinstance(fail_message, str) else ""
    involved = "move" in stages or any(
        token in message for token in ("move", "storage", "destination")
    )
    if not involved:
        return "NONE"
    return "FAILURE" if failed else "SUCCESS"


def history_script_status(value: object, stages: set[str]) -> str:
    if "script" not in stages:
        return "NONE"
    failed = isinstance(value, str) and value.lower() == "failed"
    return "FAILURE" if failed else "SUCCESS"


def history_stage_names(stage_names: list[str]) -> set[str]:
    return {name.strip().lower() for name in stage_names if name}
