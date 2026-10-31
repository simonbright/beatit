from app.services.people_mentions import extract_named_people, people_missing_from_contacts


def test_extracts_attending_from_ask_reply():
    text = (
        "The Internal Medicine attending is Dr. Sarah Chen. "
        "She saw Susan on October 29 at HGH."
    )
    people = extract_named_people(text, skip_names=["Susan Brajtman"])
    assert [p["name"] for p in people] == ["Dr. Sarah Chen"]
    assert people[0]["role"] == "Internal medicine"
    assert people[0]["kind"] == "care"


def test_extracts_name_with_role_and_family():
    text = "Sarah Chen, Internal Medicine attending. Her daughter Hadas Brajtman has the ball."
    people = extract_named_people(text)
    names = {p["name"]: p for p in people}
    assert "Sarah Chen" in names or "Dr. Sarah Chen" in names
    assert names.get("Hadas Brajtman", names.get("Hadas"))["kind"] == "family"


def test_skips_saved_contacts():
    people = extract_named_people("Ask Dr. Gupta about the scan. The attending is Dr. Chen.")
    missing = people_missing_from_contacts(
        people,
        care_team=[{"id": "1", "name": "Dr. Gupta", "role": "Oncology"}],
        family_team=[],
    )
    assert [p["name"] for p in missing] == ["Dr. Chen"]
