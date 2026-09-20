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
