"""Extract follow-ups from chats and doctor notes."""

from __future__ import annotations

from app.services.action_items import (
    action_item_fingerprint,
    extract_action_items,
    items_from_event,
    prune_noisy_extracted_items,
    resolve_due,
)


def test_resolve_due_relative_and_named():
    assert resolve_due("follow up in 2 weeks", occurred_at="2026-10-30T15:00:00-04:00") == "2026-11-13"
    assert resolve_due("CT tomorrow", occurred_at="2026-10-30T15:00:00-04:00") == "2026-10-31"
    assert resolve_due("Book MRI Monday", occurred_at="2026-10-30T15:00:00-04:00") == "2026-11-02"
    assert resolve_due("return November 4", occurred_at="2026-10-30T15:00:00-04:00") == "2026-11-04"


def test_extracts_plan_from_doctor_notes():
    notes = """
    Exam: abdomen soft.
    Plan:
    Follow up in 2 weeks with oncology.
    Send the CT disc to Dr. Gupta.
    Thanks for coming in.
    """
    items = extract_action_items(
        notes,
        occurred_at="2026-10-30T10:00:00-04:00",
        source_event_id="ev1",
        source_kind="visit",
        aggressive=True,
    )
    titles = " ".join(i["title"].lower() for i in items)
    assert "follow up" in titles
    assert "dr. gupta" in titles or "ct" in titles
    due = next(i["due_at"] for i in items if "follow up" in i["title"].lower())
    assert due == "2026-11-13"


def test_whatsapp_skips_chatter_and_keeps_actions():
    chat = items_from_event(
        {
            "id": "w1",
            "source": "whatsapp",
            "kind": "family_update",
            "summary": "Need to book the MRI Monday morning",
            "occurred_at": "2026-10-30T11:00:00-04:00",
            "recorded_by": "Hadas",
            "minor": False,
        }
    )
    thanks = items_from_event(
        {
            "id": "w2",
            "source": "whatsapp",
            "kind": "family_update",
            "summary": "Thanks Das",
            "occurred_at": "2026-10-30T11:01:00-04:00",
            "minor": True,
        }
    )
    assert chat and "mri" in chat[0]["title"].lower()
    assert chat[0]["due_at"] == "2026-11-02"
    assert thanks == []
    long = items_from_event(
        {
            "id": "w3",
            "source": "whatsapp",
            "kind": "family_update",
            "minor": False,
            "occurred_at": "2026-10-30T11:02:00-04:00",
            "summary": (
                "This morning Mom received an automated message confirming an Oncology "
                "appointment this upcoming Monday afternoon, and then Internal Medicine "
                "called to say they will decide about discharge later."
            ),
        }
    )
    assert long == []


def test_whatsapp_skips_ai_paste_and_loose_bring():
    paste = items_from_event(
        {
            "id": "w4",
            "source": "whatsapp",
            "kind": "family_update",
            "minor": False,
            "occurred_at": "2026-10-30T11:03:00-04:00",
            "recorded_by": "David Tobin",
            "summary": (
                "From OpenEvidence AI the most advanced medical AI program: Below are "
                "plain-language questions the family can bring to the next oncology visit."
            ),
        }
    )
    assert paste == []
    questions = extract_action_items(
        "Below are plain-language questions the family can bring to rounds tomorrow.",
        occurred_at="2026-10-30T11:00:00-04:00",
        source_kind="whatsapp",
    )
    assert questions == []


def test_whatsapp_skips_questions_and_finished_chatter():
    chatter = [
        "Do we call Oncology?",
        "Just got off the phone with Simon.",
        "G can also call me anytime",
        "If you need to reach me, call on regular cellphone",
        "Going to book a hotel room in Kingston",
        "Yeah I will ask the Doctor once they get here",
        "Can we get David on the phone with a Doctor?",
        "On exit if no volunteer, head for black phone on the wall.",
        "Pick up and tell them the name and room 12.",
        "Ask the Nurse for the exact address and if flowers can be brought in",
    ]
    for summary in chatter:
        items = items_from_event(
            {
                "id": "chatter",
                "source": "whatsapp",
                "kind": "family_update",
                "minor": False,
                "occurred_at": "2026-10-30T11:04:00-04:00",
                "summary": summary,
            }
        )
        assert items == [], summary
    keep = items_from_event(
        {
            "id": "keep",
            "source": "whatsapp",
            "kind": "family_update",
            "minor": False,
            "occurred_at": "2026-10-30T11:05:00-04:00",
            "recorded_by": "Hadas",
            "summary": "Call the resident managing Mom's case and ask why oncology has not seen her.",
        }
    )
    assert keep and "resident" in keep[0]["title"].lower()


def test_prune_drops_openevidence_extracts():
    profile = {
        "care_items": [
            {
                "title": "From OpenEvidence AI the most advanced medical AI program: Below are plain-language questions…",
                "source": "extracted",
                "source_kind": "whatsapp",
                "status": "open",
            },
            {
                "title": "Book MRI Monday",
                "source": "extracted",
                "source_kind": "whatsapp",
                "status": "open",
            },
        ]
    }
    assert prune_noisy_extracted_items(profile) == 1
    assert [row["title"] for row in profile["care_items"]] == ["Book MRI Monday"]


def test_fingerprint_dedupes_same_title():
    a = action_item_fingerprint("Book MRI Monday", due_at="2026-11-02", source_event_id="e1")
    b = action_item_fingerprint("Book MRI Monday", due_at="2026-11-02", source_event_id="e1")
    c = action_item_fingerprint("Book MRI Monday", due_at="2026-11-09", source_event_id="e1")
    assert a == b
    assert a != c
