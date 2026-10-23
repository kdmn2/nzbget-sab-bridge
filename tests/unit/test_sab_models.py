from __future__ import annotations

from nzbget_sab_bridge.sab.models import SabHistorySlot


def test_history_slot_null_strings_are_tolerated() -> None:
    slot = SabHistorySlot.model_validate(
        {
            "nzo_id": "synthetic",
            "name": None,
            "nzb_name": None,
            "category": None,
            "status": None,
            "size": None,
            "fail_message": None,
            "storage": None,
            "path": None,
            "url": None,
            "url_info": None,
            "password": None,
            "md5sum": None,
            "script": None,
            "script_line": None,
            "report": None,
            "action_line": None,
            "duplicate_key": None,
            "series": None,
        }
    )
    assert slot.name == ""
    assert slot.url == ""
    assert slot.series == ""
    assert slot.password == ""
