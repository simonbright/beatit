"""Family timeline: care events, merged facts, care team, WhatsApp import."""

from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

from app.services import case_manager
from app.services.care_timeline import build_care_team, build_timeline, normalize_care_event
from app.services.whatsapp_import import (
    messages_to_care_events,
    parse_whatsapp_export,
    text_from_export_zip,
)

IOS_EXPORT = """[2026-10-27, 11:06:04] Susan Health Updates: ‎Messages and calls are end-to-end encrypted.
[2026-10-27, 11:07:15] Hadas Brajtman: 1. Mom's blood pressure is too low and not improving with fluids.
[2026-10-27, 11:07:36] Hadas Brajtman: 2. Mom is getting a catheter
so they can monitor output
[2026-10-27, 11:09:36] Simon Brightman: Thanks Das
[2026-10-28, 13:03:59] Aron Brajtman: ‎<attached: 00003076-AUDIO-2026-10-28-13-03-59.opus>
[2026-10-29, 18:07:02] Aron Brajtman: 6pm update. Finished supper ‎<This message was edited>
"""


@pytest.fixture()
def patient(tmp_path, monkeypatch):
    pid = "tl-patient"
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "diagnostics": [
                    {"id": "d1", "name": "Hemoglobin", "value": 98, "unit": "g/L", "recorded_at": "2026-10-28"},
                    {"id": "d2", "name": "WBC", "value": 14.2, "unit": "10^9/L", "recorded_at": "2026-10-28"},
                ],
                "care_items": [
                    {"id": "c1", "title": "CT abdomen", "kind": "exam", "due_at": "2099-01-01", "status": "open"}
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(case_manager, "_profile_path", lambda _pid: path)
    monkeypatch.setattr(case_manager, "_patient_dir", lambda _pid: tmp_path)
    monkeypatch.setattr(case_manager, "load_registry", lambda: {"patients": [{"id": pid, "cases": []}]})
    return pid


def test_parse_ios_export_keeps_text_and_sender():
    msgs = parse_whatsapp_export(IOS_EXPORT)
    assert [m["sender"] for m in msgs] == ["Hadas Brajtman", "Hadas Brajtman", "Simon Brightman", "Aron Brajtman"]
    assert msgs[1]["text"] == "2. Mom is getting a catheter\nso they can monitor output"
    assert msgs[0]["sent_at"] == "2026-10-27T11:07:15-04:00"
    assert msgs[3]["text"] == "6pm update. Finished supper"
    assert msgs[2]["substantive"] is False  # "Thanks Das"
    assert msgs[0]["substantive"] is True


def test_parse_android_day_first():
    msgs = parse_whatsapp_export("28/10/2026, 15:14 - Simon: Oncologist rounds at 3\n29/10/2026, 09:01 - Anna: ok")
    assert msgs[0]["sent_at"].startswith("2026-10-28T15:14")


def test_zip_export():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", IOS_EXPORT)
        zf.writestr("00003076-AUDIO.opus", b"\x00\x01")
    text = text_from_export_zip(base64.b64encode(buf.getvalue()).decode())
    assert "catheter" in text


def test_import_is_idempotent(patient):
    events = messages_to_care_events(parse_whatsapp_export(IOS_EXPORT), chat_name="Susan Health Updates")
    first = case_manager.import_patient_care_events(patient, events)
    second = case_manager.import_patient_care_events(patient, events)
    assert first["added"] == 4 and first["skipped"] == 0
    assert second["added"] == 0 and second["skipped"] == 4
    profile = case_manager.get_patient_profile(patient)
    assert len(profile["care_events"]) == 4
    assert all(e["source"] == "whatsapp" for e in profile["care_events"])


ANDROID_REEXPORT = """27/10/2026, 11:07 - Hadas Brajtman: 1. Mom's blood pressure is too low and not improving with fluids.
27/10/2026, 11:07 - Hadas Brajtman: 2. Mom is getting a catheter
so they can monitor output
27/10/2026, 11:09 - Simon Brightman: Thanks Das
29/10/2026, 18:07 - Aron Brajtman: 6pm update. Finished supper ‎<This message was edited>
29/10/2026, 09:01 - Hadas Brajtman: Morning labs are back
"""


def test_full_reexport_does_not_duplicate(patient):
    first = messages_to_care_events(parse_whatsapp_export(IOS_EXPORT), chat_name="Susan Health Updates")
    case_manager.import_patient_care_events(patient, first)
    again = messages_to_care_events(
        parse_whatsapp_export(ANDROID_REEXPORT, tz_name="America/Chicago"),
        chat_name="Susan Health Updates",
    )
    result = case_manager.import_patient_care_events(patient, again)
    assert result["added"] == 1
    assert result["skipped"] == 4
    texts = [e["summary"] for e in case_manager.get_patient_profile(patient)["care_events"]]
    assert texts.count("Morning labs are back") == 1
    assert sum(1 for t in texts if t.startswith("1. Mom's blood pressure")) == 1


def test_manual_event_and_timeline_merge(patient):
    entry = case_manager.add_patient_care_event(
        patient,
        kind="visit",
        occurred_at="2026-10-28T15:15:00-04:00",
        practitioner="Dr. Gupta",
        role="Oncology",
        summary="Cultures pending. Continue IV antibiotics.",
        recorded_by="simon",
    )
    assert entry["recorded_by"] == "simon"
    profile = case_manager.get_patient_profile(patient)
    rows = build_timeline(profile, documents=[])
    kinds = {r["kind"] for r in rows}
    assert {"visit", "lab_results", "plan_due"} <= kinds
    labs = next(r for r in rows if r["kind"] == "lab_results")
    assert "Hemoglobin: 98 g/L" in labs["detail"]
    assert labs["provenance_type"] == "lab_record"
    visit = next(r for r in rows if r["kind"] == "visit")
    assert visit["provenance"] == "Recorded by simon"
    assert visit["editable"] is True
    team = build_care_team(profile["care_events"])
    assert team[0]["name"] == "Dr. Gupta" and team[0]["contacts"] == 1


def test_update_marks_edit_and_validates(patient):
    entry = case_manager.add_patient_care_event(
        patient, kind="note", occurred_at="2026-10-28", summary="Ate lunch", recorded_by="anna"
    )
    updated = case_manager.update_patient_care_event(
        patient, entry["id"], updated_by="simon", kind="status", summary="Ate most of lunch"
    )
    assert updated["kind"] == "status" and updated["updated_by"] == "simon"
    with pytest.raises(ValueError):
        case_manager.update_patient_care_event(patient, entry["id"], summary="", practitioner="")
    assert case_manager.delete_patient_care_event(patient, entry["id"])
    assert case_manager.get_patient_profile(patient)["care_events"] == []


def test_normalize_rejects_empty():
    assert normalize_care_event({"kind": "visit"}) is None


# --- Care team directory -------------------------------------------------


def test_team_member_crud_and_rename_updates_entries(patient):
    m = case_manager.add_patient_team_member(
        patient, name="Dr. G", role="Oncology", environments="Cancer centre, Hospital — inpatient"
    )
    assert m["environments"] == ["Cancer centre", "Hospital — inpatient"]
    with pytest.raises(ValueError):
        case_manager.add_patient_team_member(patient, name="dr g")
    case_manager.add_patient_care_event(
        patient, kind="visit", occurred_at="2026-10-29T09:00:00-04:00", practitioner="Dr. G", summary="Rounds"
    )
    renamed = case_manager.update_patient_team_member(patient, m["id"], name="Dr. Gupta")
    assert renamed["name"] == "Dr. Gupta"
    profile = case_manager.get_patient_profile(patient)
    assert profile["care_events"][0]["practitioner"] == "Dr. Gupta"
    team = build_care_team(profile["care_events"], profile["care_team"])
    assert team[0]["member_id"] == m["id"] and team[0]["contacts"] == 1
    assert case_manager.delete_patient_team_member(patient, m["id"])
    assert case_manager.get_patient_profile(patient)["care_events"][0]["practitioner"] == "Dr. Gupta"


def test_logging_a_visit_adds_new_person_to_team(patient):
    case_manager.add_patient_care_event(
        patient,
        kind="visit",
        occurred_at="2026-10-29T09:00:00-04:00",
        practitioner="Nurse Amy",
        role="Nursing",
        location="Level 9",
        summary="Vitals stable",
    )
    team = case_manager.get_patient_profile(patient)["care_team"]
    assert [(m["name"], m["role"], m["environments"]) for m in team] == [("Nurse Amy", "Nursing", ["Level 9"])]


# --- Visit transcripts ----------------------------------------------------

from app.services.transcript_import import speakers, transcript_text  # noqa: E402

VTT = """WEBVTT

1
00:00:01.000 --> 00:00:04.000
<v Dr. Lee>Her white count is coming down.</v>

2
00:00:04.500 --> 00:00:07.000
<v Dr. Lee>We'll keep the antibiotics another 48 hours.</v>

3
00:00:07.500 --> 00:00:09.000
<v Simon>When is the next CT?</v>
"""


def test_vtt_transcript_keeps_words_and_speakers():
    text = transcript_text(text=VTT)
    assert text.splitlines() == [
        "Dr. Lee: Her white count is coming down. We'll keep the antibiotics another 48 hours.",
        "Simon: When is the next CT?",
    ]
    assert speakers(text) == ["Dr. Lee", "Simon"]


def test_srt_and_docx_transcripts():
    srt = "1\n00:00:01,000 --> 00:00:03,000\nNurse: Pressure is 110 over 70.\n\n2\n00:00:03,500 --> 00:00:05,000\nNurse: She ate half her lunch.\n"
    assert transcript_text(file_base64=base64.b64encode(srt.encode()).decode(), filename="visit.srt") == (
        "Nurse: Pressure is 110 over 70. She ate half her lunch."
    )
    doc_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        "<w:p><w:r><w:t>Dr. Lee: Plan is unchanged.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Aron: Thank you.</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
    out = transcript_text(file_base64=base64.b64encode(buf.getvalue()).decode(), filename="visit.docx")
    assert out == "Dr. Lee: Plan is unchanged.\nAron: Thank you."
    with pytest.raises(ValueError):
        transcript_text(file_base64="aGk=", filename="visit.m4a")


def test_pdf_transcript_keeps_words():
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(
        0,
        10,
        "Dr. Lee: Plan is unchanged. Continue IV antibiotics and check the white count again tomorrow morning.",
    )
    data = pdf.output()
    if isinstance(data, str):
        data = data.encode("latin-1")
    out = transcript_text(file_base64=base64.b64encode(data).decode(), filename="visit.pdf")
    assert "Plan is unchanged" in out


def test_source_documents_are_kept_together(patient):
    entry = case_manager.add_patient_care_event(
        patient,
        kind="transcript",
        occurred_at="2026-10-29T17:40:00-04:00",
        practitioner="Dr. Lee",
        summary="Rounds",
        source_document_id="doc-1",
        source_document_title="Notes.txt",
        source_documents=[
            {"id": "doc-1", "title": "Notes.txt"},
            {"id": "doc-2", "title": "Consult.pdf"},
        ],
        source="transcript",
    )
    assert [d["id"] for d in entry["source_documents"]] == ["doc-1", "doc-2"]
    profile = case_manager.get_patient_profile(patient)
    docs = [
        {"id": "doc-1", "title": "Notes.txt", "source_type": "text", "created_at": "2026-10-29T21:41:00+00:00"},
        {"id": "doc-2", "title": "Consult.pdf", "source_type": "pdf", "created_at": "2026-10-29T21:42:00+00:00"},
        {"id": "doc-3", "title": "CT report", "source_type": "pdf", "created_at": "2026-10-29T21:43:00+00:00"},
    ]
    rows = build_timeline(profile, docs)
    visit = next(r for r in rows if r.get("care_event_id") == entry["id"])
    assert [d["id"] for d in visit["source_documents"]] == ["doc-1", "doc-2"]
    assert [r["title"] for r in rows if r["category"] == "document"] == ["CT report"]


def test_visit_notes_use_llm_bullets():
    import asyncio

    from app.services.visit_summary import summarize_visit_notes

    class FakeLLM:
        async def generate(self, **_kwargs):
            return "- White count is coming down\n- Keep antibiotics another 48 hours\n- Next CT is not scheduled"

    text, source = asyncio.run(
        summarize_visit_notes(
            [("notes.txt", "Dr. Lee: Her white count is coming down. Keep antibiotics 48 hours. CT not scheduled.")],
            llm=FakeLLM(),
            who="Dr. Lee",
            role="Oncology",
        )
    )
    assert source == "ai"
    assert text.startswith("- ")
    assert "white count" in text.lower()


def test_visit_notes_fallback_to_bullets():
    from app.services.visit_summary import fallback_visit_bullets, format_visit_bullets, parse_visit_bullets

    text = (
        "Dr. Lee: Her white count is coming down and we will keep the antibiotics another 48 hours.\n"
        "Simon: When is the next CT?\n"
        "Dr. Lee: Not scheduled yet. Continue IV fluids overnight."
    )
    out = fallback_visit_bullets([("notes.txt", text)])
    lines = out.splitlines()
    assert 2 <= len(lines) <= 6
    assert all(line.startswith("- ") for line in lines)
    assert "white count" in out.lower()
    assert parse_visit_bullets("1. First fact\n- Second fact") == ["First fact", "Second fact"]
    assert format_visit_bullets(["Keep antibiotics", "Keep antibiotics"]) == "- Keep antibiotics."


def test_plain_text_untouched():
    raw = "Dr. Lee: 10:30 rounds — plan unchanged.\n\nSimon: Thanks."
    assert transcript_text(text=raw) == raw


def test_visit_takes_role_from_team(patient):
    case_manager.add_patient_team_member(patient, name="Dr. Lee", role="Oncology")
    entry = case_manager.add_patient_care_event(
        patient, kind="visit", occurred_at="2026-10-29", practitioner="dr lee", summary="Rounds"
    )
    assert entry["role"] == "Oncology"


def test_linked_document_not_listed_twice(patient):
    case_manager.add_patient_care_event(
        patient,
        kind="visit",
        occurred_at="2026-10-29T17:40:00-04:00",
        practitioner="Dr. Lee",
        summary="Rounds",
        source_document_id="doc-1",
        source_document_title="Visit transcript",
    )
    profile = case_manager.get_patient_profile(patient)
    docs = [
        {"id": "doc-1", "title": "Visit transcript", "source_type": "text", "created_at": "2026-10-29T21:41:00+00:00"},
        {"id": "doc-2", "title": "CT report", "source_type": "pdf", "created_at": "2026-10-29T21:42:00+00:00"},
    ]
    doc_rows = [r for r in build_timeline(profile, docs) if r["category"] == "document"]
    assert [r["title"] for r in doc_rows] == ["CT report"]


def test_team_contact_and_preferred_method(patient):
    m = case_manager.add_patient_team_member(
        patient,
        name="Dr. G",
        role="Oncology",
        phone="613-555-0142 ext. 4410",
        email="drg@example.org",
        contact_other="Pager 221",
        preferred_contact="Text",
    )
    assert m["preferred_contact"] == "text"
    assert m["email"] == "drg@example.org" and m["contact_other"] == "Pager 221"
    updated = case_manager.update_patient_team_member(patient, m["id"], preferred_contact="carrier pigeon")
    assert updated["preferred_contact"] == ""  # unknown methods are dropped, not stored
    team = build_care_team([], case_manager.get_patient_profile(patient)["care_team"])
    assert team[0]["phone"].startswith("613") and team[0]["email"] == "drg@example.org"


def test_team_whatsapp_and_avatar(patient):
    m = case_manager.add_patient_team_member(
        patient,
        name="Dr. Yoda",
        phone="416-555-0100",
        whatsapp="14165550100",
        preferred_contact="WhatsApp",
        avatar_key="sw-yoda",
    )
    assert m["preferred_contact"] == "whatsapp"
    assert m["whatsapp"] == "14165550100"
    assert m["avatar_key"] == "sw-yoda"
    assert m["avatar_emoji"] == "🟢"
    team = build_care_team([], [m])
    assert team[0]["whatsapp"] == "14165550100"
    assert team[0]["avatar_emoji"] == "🟢"
    updated = case_manager.update_patient_team_member(patient, m["id"], avatar_key="simpsons-homer")
    assert updated["avatar_key"] == "simpsons-homer" and updated["avatar_emoji"] == "🍩"


def test_family_whatsapp_avatar_and_photo(patient):
    hadas = case_manager.add_patient_family_member(
        patient,
        name="Hadas",
        relationship="Daughter",
        phone="416-555-0199",
        whatsapp="14165550199",
        preferred_contact="whatsapp",
        avatar_key="simpsons-lisa",
    )
    assert hadas["whatsapp"] == "14165550199"
    assert hadas["avatar_key"] == "simpsons-lisa"
    assert hadas["avatar_emoji"] == "🎷"
    path = case_manager.save_member_avatar(
        patient, "family-team", hadas["id"], b"fakepng", "image/png", "face.png"
    )
    assert path.exists() and path.suffix == ".png"
    assert case_manager.member_avatar_url(patient, "family-team", hadas["id"])
    case_manager.delete_patient_family_member(patient, hadas["id"])
    assert case_manager.find_member_avatar(patient, "family-team", hadas["id"]) is None


# --- Family care team -----------------------------------------------------

from app.services.care_timeline import family_roles_payload  # noqa: E402


def test_family_roles_board_and_history(patient):
    hadas = case_manager.add_patient_family_member(
        patient, by="simon", name="Hadas", relationship="Daughter", roles=["Point person", "bedside"]
    )
    aron = case_manager.add_patient_family_member(patient, by="simon", name="Aron", relationship="Spouse")
    assert hadas["roles"] == ["point_person", "bedside"]
    assert set(hadas["role_since"]) == {"point_person", "bedside"}
    with pytest.raises(ValueError):
        case_manager.add_patient_family_member(patient, name="hadas")

    # Hand point person over to Aron from the role board
    case_manager.set_patient_family_role(patient, "point_person", [aron["id"]], by="hadas")
    profile = case_manager.get_patient_profile(patient)
    board = {r["key"]: r for r in family_roles_payload(profile["family_team"])}
    assert [h["name"] for h in board["point_person"]["holders"]] == ["Aron"]
    assert [h["name"] for h in board["bedside"]["holders"]] == ["Hadas"]
    assert board["records"]["holders"] == []  # unassigned shows on the board

    history = profile["family_role_history"]
    assert sorted((h["person"], h["role"], h["action"]) for h in history) == sorted(
        [
            ("Hadas", "point_person", "assigned"),
            ("Hadas", "bedside", "assigned"),
            ("Hadas", "point_person", "unassigned"),
            ("Aron", "point_person", "assigned"),
        ]
    )
    assert history[-1]["by"] == "hadas"
    rows = [r for r in build_timeline(profile, []) if r["kind"] == "role_change"]
    titles = {r["title"] for r in rows}
    assert "Aron — Point person" in titles and "Hadas no longer Point person" in titles


def test_family_custom_role_and_removal(patient):
    m = case_manager.add_patient_family_member(patient, name="Dov", roles=["Pharmacy runs"])
    assert m["roles"] == ["Pharmacy runs"]
    board = family_roles_payload(case_manager.get_patient_profile(patient)["family_team"])
    assert any(r["key"] == "Pharmacy runs" and r["custom"] for r in board)
    case_manager.delete_patient_family_member(patient, m["id"], by="simon")
    profile = case_manager.get_patient_profile(patient)
    assert profile["family_team"] == []
    assert profile["family_role_history"][-1]["action"] == "unassigned"


# --- What's missing ---------------------------------------------------------

from datetime import datetime as _dt  # noqa: E402

from app.services.care_timeline import build_gaps  # noqa: E402

NOW = _dt.fromisoformat("2026-10-29T20:00:00-04:00")


def _kinds(gaps, day=None):
    return sorted(i["kind"] for i in gaps["items"] if (day is None or i.get("day") == day))


def test_gaps_without_stay_prompts_admission(patient):
    gaps = build_gaps(case_manager.get_patient_profile(patient), now=NOW)
    assert gaps["stay"] is None
    assert "no_stay" in _kinds(gaps)


def test_gaps_flag_days_with_no_visit_or_tests(patient):
    add = case_manager.add_patient_care_event
    add(patient, kind="admission", occurred_at="2026-10-26T15:00:00-04:00", location="KGH", summary="Admitted")
    add(patient, kind="visit", occurred_at="2026-10-27T10:00:00-04:00", practitioner="Dr. G", role="Oncology", summary="Rounds")
    add(patient, kind="test_done", occurred_at="2026-10-27T16:00:00-04:00", summary="CT abdomen")
    add(patient, kind="note", occurred_at="2026-10-28T09:00:00-04:00", summary="Ate breakfast")
    gaps = build_gaps(case_manager.get_patient_profile(patient), now=NOW)
    assert gaps["stay"]["active"] and gaps["stay"]["days"] == 4
    # Oct 27: visit + test recorded → only the family update is missing
    assert _kinds(gaps, "2026-10-27") == ["no_family_update"]
    # Oct 28: labs exist in the fixture (Hemoglobin, WBC) → tests covered; no visit
    assert _kinds(gaps, "2026-10-28") == ["no_visit"]
    no_tests_27 = [i for i in gaps["items"] if i.get("day") == "2026-10-27" and i["kind"] == "no_tests"]
    assert no_tests_27 == []
    # Oct 29 (today): nothing yet, flagged softly
    today = [i for i in gaps["items"] if i.get("day") == "2026-10-29"]
    assert {i["kind"] for i in today} == {"no_visit", "no_tests", "no_family_update"}
    assert all(i["severity"] == "info" and "today so far" in i["text"] for i in today)
    # CT on Oct 27 is followed by lab results on Oct 28 → not pending
    assert "result_pending" not in _kinds(gaps)
    # Dr. G last seen Oct 27, two days ago
    assert any(i["kind"] == "not_seen" and "Dr. G (Oncology)" in i["text"] for i in gaps["items"])
    # Key family roles uncovered (no family team yet → info)
    assert _kinds(gaps).count("role_unassigned") == 2


def test_gaps_pending_result_and_discharge(patient):
    add = case_manager.add_patient_care_event
    add(patient, kind="admission", occurred_at="2026-10-29T08:00:00-04:00", summary="Admitted")
    add(patient, kind="test_ordered", occurred_at="2026-10-28T09:00:00-04:00", summary="MRI liver")
    gaps = build_gaps(case_manager.get_patient_profile(patient), now=NOW)
    pending = [i for i in gaps["items"] if i["kind"] == "result_pending"]
    assert pending and "MRI liver" in pending[0]["text"]
    add(patient, kind="result", occurred_at="2026-10-29T12:00:00-04:00", summary="MRI: no new lesions")
    add(patient, kind="discharge", occurred_at="2026-10-29T18:00:00-04:00", summary="Home")
    gaps = build_gaps(case_manager.get_patient_profile(patient), now=NOW)
    assert "result_pending" not in _kinds(gaps)
    assert gaps["stay"]["active"] is False
    assert "not_seen" not in _kinds(gaps)



def test_gap_hint_when_chat_mentions_care_team(patient):
    case_manager.add_patient_care_event(patient, kind="admission", occurred_at="2026-10-28T08:00:00-04:00", summary="Admitted")
    events = messages_to_care_events(
        parse_whatsapp_export("[2026-10-28, 15:34:00] Hadas: The infectious disease doctor examined Mom, cultures pending")
    )
    case_manager.import_patient_care_events(patient, events)
    gaps = build_gaps(case_manager.get_patient_profile(patient), now=NOW)
    visit_gap = next(i for i in gaps["items"] if i.get("day") == "2026-10-28" and i["kind"] == "no_visit")
    assert visit_gap["severity"] == "warn" and "1 family message mentions the care team" in visit_gap["text"]
