"""Who has the ball, visit close-out, and the Ask workspace brief."""

from __future__ import annotations

import json

import pytest

from app.services import case_manager
from app.services.care_timeline import CARE_EVENT_KINDS, kinds_payload, normalize_care_event_kind
from app.services.care_workspace import (
    DEFAULT_BALL_KIND,
    build_care_workspace_brief,
    complete_visit_actions,
    current_ball_holders,
    find_internal_medicine_team,
    is_professional_visit_kind,
    normalize_ball_checkin,
    normalize_ball_holders,
    normalize_ball_kind,
    normalize_visit_actions,
)


@pytest.fixture()
def patient(tmp_path, monkeypatch):
    pid = "ask-patient"
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "care_team": [
                    {"id": "t1", "name": "Dr. Gupta", "role": "Oncology"},
                    {"id": "t2", "name": "", "role": "Internal medicine (internist)"},
                ],
                "family_team": [{"id": "f1", "name": "Hadas", "relationship": "Daughter", "roles": ["point_person"]}],
                "care_items": [
                    {
                        "id": "c1",
                        "title": "Send CT to Dr. Gupta",
                        "due_at": "2026-11-02",
                        "status": "open",
                    }
                ],
                "care_events": [
                    {
                        "id": "v1",
                        "kind": "visit",
                        "practitioner": "Dr. Gupta",
                        "role": "Oncology",
                        "summary": "Continuing antibiotics. Discussed discharge after cultures.",
                        "occurred_at": "2026-10-30T14:00:00-04:00",
                    },
                    {
                        "id": "v2",
                        "kind": "visit",
                        "practitioner": "",
                        "role": "Internal medicine",
                        "summary": "Internist on rounds — attending name not given.",
                        "occurred_at": "2026-10-29T09:00:00-04:00",
                    },
                ],
                "ball_holders": [
                    {
                        "id": "b1",
                        "name": "Hadas",
                        "role": "Point person",
                        "since": "2026-10-28",
                    }
                ],
                "doctor_questions": [
                    {"id": "q1", "text": "What is the attending name?", "doctor": "Internal medicine", "status": "open"}
                ],
                "diagnostics": [
                    {"id": "d1", "name": "Hemoglobin", "value": 98, "unit": "g/L", "recorded_at": "2026-10-28"}
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(case_manager, "_profile_path", lambda _pid: path)
    monkeypatch.setattr(case_manager, "_patient_dir", lambda _pid: tmp_path)
    monkeypatch.setattr(case_manager, "load_registry", lambda: {"patients": [{"id": pid, "cases": []}]})
    case_manager._profile_cache_clear(pid)
    return pid


def test_vitals_is_a_timeline_kind():
    assert "vitals" in CARE_EVENT_KINDS
    assert normalize_care_event_kind("vitals") == "vitals"
    assert any(k["value"] == "vitals" for k in kinds_payload())


def test_normalize_ball_holders_keeps_people_and_dates():
    holders = normalize_ball_holders(
        [
            {
                "name": "Hadas",
                "role": "Point person — daily plan",
                "since": "2026-10-28T09:00:00",
                "title": "Daughter",
                "success": "Family has tonight's plan",
            },
            {
                "name": "Dr. Gupta",
                "since": "2026-10-30",
                "until": "2026-11-02",
                "notes": "awaiting CT",
                "title": "Attending",
                "department": "Infectious disease",
            },
            {"name": ""},
        ]
    )
    assert [h["name"] for h in holders] == ["Hadas", "Dr. Gupta"]
    assert holders[0]["since"] == "2026-10-28T09:00"
    assert holders[0]["title"] == "Daughter"
    assert holders[0]["success"] == "Family has tonight's plan"
    assert holders[1]["until"] == "2026-11-02"
    assert holders[1]["department"] == "Infectious disease"
    now = current_ball_holders(holders + [{"name": "Old", "until": "2026-10-01"}], today="2026-10-31")
    assert [h["name"] for h in now] == ["Hadas", "Dr. Gupta"]
    still = current_ball_holders(
        [{"name": "Chen", "until": "2026-10-31T14:00"}],
        today="2026-10-31T13:00",
    )
    assert [h["name"] for h in still] == ["Chen"]
    gone = current_ball_holders(
        [{"name": "Chen", "until": "2026-10-31T14:00"}],
        today="2026-10-31T15:00",
    )
    assert gone == []
    led = normalize_ball_holders(
        [
            {"name": "Aron", "role": "Bedside"},
            {"name": "Hadas", "role": "Point person", "lead": True},
        ]
    )
    assert led[0]["name"] == "Hadas" and led[0]["lead"]
    assert led[1]["name"] == "Aron" and not led[1]["lead"]


def test_visit_actions_are_optional():
    assert complete_visit_actions([]) == []
    assert complete_visit_actions([{"title": "Call the internist"}]) == [
        {"title": "Call the internist"}
    ]
    assert complete_visit_actions(
        [{"title": "Call the internist", "due_at": "2026-11-01"}]
    ) == [{"title": "Call the internist", "due_at": "2026-11-01"}]
    assert is_professional_visit_kind("transcript")
    assert not is_professional_visit_kind("vitals")


def test_normalize_visit_actions_needs_title():
    assert normalize_visit_actions([{"title": "  ", "due_at": "2026-11-01"}]) == []
    items = normalize_visit_actions([{"title": "Send CT", "due_at": "2026-11-01T15:00:00"}])
    assert items[0]["due_at"] == "2026-11-01T15:00"


def test_care_item_keeps_due_time(patient):
    item = case_manager.add_patient_care_item(
        patient,
        title="Call internist",
        due_at="2026-11-01 16:30",
        contact="Simon",
    )
    assert item["due_at"] == "2026-11-01T16:30"
    updated = case_manager.update_patient_care_item(patient, item["id"], due_at="2026-11-01")
    assert updated["due_at"] == "2026-11-01"


def test_internal_medicine_without_attending_name():
    found = find_internal_medicine_team(
        [{"name": "", "role": "Internal medicine (internist)"}],
        [{"practitioner": "", "role": "Hospitalist"}],
    )
    assert found["named"] == []
    assert found["unnamed_roles"]


def test_ball_holders_persist(patient):
    saved = case_manager.set_patient_ball_holders(
        patient,
        [
            {
                "name": "Aron",
                "role": "Bedside coverage tonight",
                "success": "Someone is in the room until 8am",
                "title": "Son-in-law",
                "department": "Family",
                "since": "2026-10-31T18:00",
                "until": "2026-11-01T08:00",
            }
        ],
    )
    holder = saved["ball_holders"][0]
    assert holder["name"] == "Aron"
    assert holder["title"] == "Son-in-law"
    assert holder["success"] == "Someone is in the room until 8am"
    assert holder["department"] == "Family"
    stored = case_manager.get_patient_profile(patient)
    assert stored["ball_holders"][0]["since"] == "2026-10-31T18:00"
    assert stored["ball_holders"][0]["until"] == "2026-11-01T08:00"
    assert stored["ball_kind"] == "basketball"
    soccer = case_manager.set_patient_ball_holders(patient, saved["ball_holders"], ball_kind="soccer")
    assert soccer["ball_kind"] == "soccer"
    assert case_manager.get_patient_profile(patient)["ball_kind"] == "soccer"
    with_checkin = case_manager.set_patient_ball_holders(
        patient,
        saved["ball_holders"],
        checkin={"who": "Hadas", "what": "Confirm IM follow-up on biopsy", "at": "2026-11-01T09:00"},
    )
    assert with_checkin["ball_checkin"] == {
        "who": "Hadas",
        "what": "Confirm IM follow-up on biopsy",
        "at": "2026-11-01T09:00",
    }
    cleared = case_manager.set_patient_ball_holders(patient, saved["ball_holders"], checkin={})
    assert cleared.get("ball_checkin") in (None, {})


def test_normalize_ball_checkin_needs_something():
    assert normalize_ball_checkin({}) is None
    assert normalize_ball_checkin({"who": "Simon", "when": "2026-11-01T18:30"})["at"] == "2026-11-01T18:30"


def test_ball_kind_defaults_to_basketball():
    assert DEFAULT_BALL_KIND == "basketball"
    assert normalize_ball_kind("") == "basketball"
    assert normalize_ball_kind("hoops") == "basketball"
    assert normalize_ball_kind("futbol") == "soccer"
    assert normalize_ball_kind("not-a-ball") == "basketball"


def test_attach_visit_actions(patient):
    added = case_manager.attach_event_actions(
        patient,
        "v1",
        [{"title": "Culture follow-up", "due_at": "2026-11-01"}],
    )
    assert added
    profile = case_manager.get_patient_profile(patient)
    closeout = case_manager.event_closeout_items(profile, "v1")
    assert any(i["title"] == "Culture follow-up" for i in closeout)


def test_workspace_brief_answers_defaults(patient):
    brief = build_care_workspace_brief(patient)
    assert "Hadas" in brief
    assert "from 2026-10-28" in brief
    assert "Send CT to Dr. Gupta" in brief
    assert "Internal Medicine" in brief
    assert "no attending name" in brief.lower() or "not saved" in brief.lower()
    assert "discharge" in brief.lower()
    assert "Hemoglobin" in brief
