from app.services.payload.panel_result import normalize_schedule_result_for_panel


def test_normalize_adds_panel_aliases_and_int_ids():
    raw = {
        "date": "2026-09-20",
        "assigned_prid_list": ["5", "6"],
        "unassigned_prid_list": ["7"],
        "assigned_crid_list": ["1"],
        "unassigned_crid_list": [],
        "removed_prid_list": [],
        "removed_crid_list": ["2"],
        "caregiver_schedules": {
            "1": {
                "cid": "20260716000034",
                "crid": "1",
                "visits": [
                    {
                        "prid": "5",
                        "pid": "10",
                        "start_time": 60,
                        "end_time": 120,
                        "crid": "1",
                    }
                ],
            }
        },
    }

    out = normalize_schedule_result_for_panel(raw)

    assert out["assigned_prids"] == [5, 6]
    assert out["unassigned_prids"] == [7]
    assert out["removed_caregivers"] == [2]
    assert out["caregiver_schedules"]["1"]["cid"] == 20260716000034
    assert out["caregiver_schedules"]["1"]["crid"] == 1
    assert out["caregiver_schedules"]["1"]["visits"][0]["prid"] == 5
    assert out["caregiver_schedules"]["1"]["visits"][0]["pid"] == 10


def test_normalize_nested_result_wrapper():
    wrapped = {
        "status": "completed",
        "result": {
            "assigned_prid_list": ["1"],
            "unassigned_prid_list": [],
            "caregiver_schedules": {},
        },
    }
    out = normalize_schedule_result_for_panel(wrapped)
    assert out["result"]["assigned_prids"] == [1]


def test_normalize_passthrough_non_schedule():
    assert normalize_schedule_result_for_panel(None) is None
    assert normalize_schedule_result_for_panel({"ok": True}) == {"ok": True}


def test_normalize_backfills_assigned_lists_from_schedules():
    """multicpsat bug: visits present but assigned_*_list empty → Panel projects 0."""
    raw = {
        "date": "-",
        "assigned_crid_list": [],
        "assigned_prid_list": [],
        "unassigned_prid_list": [],
        "unassigned_crid_list": [],
        "removed_prid_list": [],
        "removed_crid_list": [],
        "caregiver_schedules": {
            "16": {
                "cid": "20260922000104",
                "crid": "16",
                "visits": [
                    {
                        "prid": "398",
                        "start_time": 908,
                        "end_time": 968,
                        "duration": 60,
                        "crid": "16",
                        "travel_time": 10,
                        "waiting_time": 0,
                    }
                ],
            },
            "18": {
                "cid": "20260922000109",
                "crid": "18",
                "visits": [],
            },
        },
    }

    out = normalize_schedule_result_for_panel(raw)

    assert out["assigned_prids"] == [398]
    assert out["assigned_prid_list"] == ["398"]
    assert out["assigned_crids"] == [16]
    assert out["assigned_crid_list"] == ["16"]
    assert out["caregiver_schedules"]["16"]["cid"] == 20260922000104
    assert out["caregiver_schedules"]["16"]["visits"][0]["prid"] == 398

