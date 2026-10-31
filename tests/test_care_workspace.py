"""Who has the ball, visit close-out, and the Ask workspace brief."""

from __future__ import annotations

import json

import pytest

from app.services import case_manager
from app.services.care_timeline import CARE_EVENT_KINDS, kinds_payload, normalize_care_event_kind
from app.services.care_workspace import (
    build_care_workspace_brief,
    complete_visit_actions,
    find_internal_medicine_team,
    is_professional_visit_kind,
    normalize_ball_holders,
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
            {"name": "Hadas", "role": "Point person", "since": "2026-10-28T09:00:00"},
            {"name": "Dr. Gupta", "since": "2026-10-30", "until": "2026-11-02", "notes": "awaiting CT"},
            {"name": ""},
        ]
    )
    assert [h["name"] for h in holders] == ["Hadas", "Dr. Gupta"]
    assert holders[0]["since"] == "2026-10-28"
    assert holders[1]["until"] == "2026-11-02"


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
        [{"name": "Aron", "role": "Bedside", "since": "2026-10-31"}],
    )
    assert saved["ball_holders"][0]["name"] == "Aron"
    assert case_manager.get_patient_profile(patient)["ball_holders"][0]["since"] == "2026-10-31"


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
    assert "since 2026-10-28" in brief
    assert "Send CT to Dr. Gupta" in brief
    assert "Internal Medicine" in brief
    assert "no attending name" in brief.lower() or "not saved" in brief.lower()
    assert "discharge" in brief.lower()
    assert "Hemoglobin" in brief
