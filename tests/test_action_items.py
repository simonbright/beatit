"""Extract follow-ups from chats and doctor notes."""

from __future__ import annotations

from app.services.action_items import (
    action_item_fingerprint,
    extract_action_items,
    items_from_event,
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


def test_fingerprint_dedupes_same_title():
    a = action_item_fingerprint("Book MRI Monday", due_at="2026-11-02", source_event_id="e1")
    b = action_item_fingerprint("Book MRI Monday", due_at="2026-11-02", source_event_id="e1")
    c = action_item_fingerprint("Book MRI Monday", due_at="2026-11-09", source_event_id="e1")
    assert a == b
    assert a != c
