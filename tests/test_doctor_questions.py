"""Questions to ask the doctor, stored on the patient profile."""

from __future__ import annotations

import json

from app.services import case_manager


def test_add_question_fills_role_from_care_team(tmp_path, monkeypatch):
    pid = "q-patient"
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "care_team": [
                    {"id": "t1", "name": "Dr. Gupta", "role": "Oncology"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(case_manager, "_profile_path", lambda _pid: path)
    monkeypatch.setattr(case_manager, "_patient_dir", lambda _pid: tmp_path)
    monkeypatch.setattr(case_manager, "load_registry", lambda: {"patients": [{"id": pid, "cases": []}]})

    entry = case_manager.add_patient_doctor_question(
        pid,
        question="What does the latest scan change?",
        doctor="Dr. Gupta",
        recorded_by="simon@x.com",
    )
    assert entry["doctor"] == "Dr. Gupta"
    assert entry["role"] == "Oncology"
    assert entry["status"] == "open"
    assert entry["recorded_by"] == "simon@x.com"

    asked = case_manager.update_patient_doctor_question(pid, entry["id"], status="asked")
    assert asked["status"] == "asked"
    assert asked["asked_at"]

    answered = case_manager.update_patient_doctor_question(
        pid, entry["id"], status="answered", answer="Stay on the current plan."
    )
    assert answered["status"] == "answered"
    assert answered["answer"] == "Stay on the current plan."

    reopened = case_manager.update_patient_doctor_question(pid, entry["id"], status="open")
    assert reopened["status"] == "open"
    assert reopened["asked_at"] is None

    removed = case_manager.delete_patient_doctor_question(pid, entry["id"])
    assert removed["id"] == entry["id"]
    assert case_manager.list_patient_doctor_questions(pid) == []


def test_blank_question_is_rejected(tmp_path, monkeypatch):
    pid = "q-patient"
    path = tmp_path / "profile.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(case_manager, "_profile_path", lambda _pid: path)
    monkeypatch.setattr(case_manager, "_patient_dir", lambda _pid: tmp_path)
    monkeypatch.setattr(case_manager, "load_registry", lambda: {"patients": [{"id": pid, "cases": []}]})
    try:
        case_manager.add_patient_doctor_question(pid, question="   ")
    except ValueError as exc:
        assert "question" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")
