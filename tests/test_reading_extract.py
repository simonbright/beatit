"""Pull vitals out of chats and visit notes."""

from app.services.reading_extract import extract_readings, readings_from_event, store_extracted_readings


def test_extracts_temp_hr_bp_o2():
    items = extract_readings(
        "Temp 37.2 C, HR 82, BP 118/74, O2 sat 97%",
        occurred_at="2026-10-30T15:10:00-04:00",
        source_event_id="e1",
        source_kind="whatsapp",
        contact="Hadas",
    )
    by_name = {row["name"]: row for row in items}
    assert by_name["Temperature"]["value"] == 37.2
    assert by_name["Temperature"]["unit"] == "°C"
    assert by_name["Heart rate"]["value"] == 82
    assert by_name["Systolic BP"]["value"] == 118
    assert by_name["Diastolic BP"]["value"] == 74
    assert by_name["Oxygen saturation"]["value"] == 97
    assert by_name["Temperature"]["notes"] == "From chat · Hadas"
    assert by_name["Temperature"]["source"] == "extracted"


def test_skips_dates_and_chatter():
    items = extract_readings(
        "See you 10/30. Room 12. Thanks Das.",
        occurred_at="2026-10-30T11:00:00-04:00",
        source_kind="whatsapp",
    )
    assert items == []


def test_fahrenheit_and_visit_source():
    items = readings_from_event(
        {
            "id": "v1",
            "kind": "visit",
            "source": "manual",
            "occurred_at": "2026-10-30T09:00:00-04:00",
            "practitioner": "Dr. Gupta",
            "summary": "Afebrile. Temperature 98.6 F. Pulse ox 96.",
        }
    )
    by_name = {row["name"]: row for row in items}
    assert by_name["Temperature"]["unit"] == "°F"
    assert by_name["Oxygen saturation"]["value"] == 96
    assert by_name["Temperature"]["source_kind"] == "visit"
    assert "From visit" in by_name["Temperature"]["notes"]


def test_store_dedupes_same_reading():
    profile = {"diagnostics": []}
    incoming = extract_readings(
        "HR 80",
        occurred_at="2026-10-30T10:00:00",
        source_event_id="e9",
        source_kind="whatsapp",
    )
    assert store_extracted_readings(profile, incoming) == 1
    assert store_extracted_readings(profile, incoming) == 0
    assert len(profile["diagnostics"]) == 1
