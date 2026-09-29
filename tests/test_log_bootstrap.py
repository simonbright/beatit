"""Fast Home Log bootstrap should skip diagnostics enrichment."""

from __future__ import annotations

from app.services.case_manager import get_patient_log_bootstrap, get_patient_profile


def test_log_bootstrap_skips_diagnostics(tmp_path, monkeypatch):
    patient_id = "test-patient"
    profile_dir = tmp_path / "patients" / patient_id
    profile_dir.mkdir(parents=True)
    profile_path = profile_dir / "profile.json"
    profile_path.write_text(
        """
        {
          "log_tile_order": ["feel-fine", "nauseous"],
          "log_custom_tiles": [{"id": "abc", "label": "Headache", "scale": true}],
          "log_tile_hidden": ["vomiting"],
          "journal": [
            {"id": "j1", "label": "Feel Fine", "recorded_at": "2026-09-28T12:00:00"},
            {"id": "j2", "label": "Nauseous", "severity": 2, "recorded_at": "2026-09-27T12:00:00"}
          ],
          "medications": [
            {"id": "m1", "name": "CBD 1 drop", "status": "active", "show_on_log": true}
          ],
          "diagnostics": [
            {"id": "d1", "name": "Iron", "value": 101, "unit": "mcg/dL", "recorded_at": "2026-09-11"}
          ]
        }
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.services.case_manager._profile_path",
        lambda pid: profile_path if pid == patient_id else profile_path,
    )

    lite = get_patient_log_bootstrap(patient_id)
    assert lite["log_tile_order"] == ["feel-fine", "nauseous"]
    assert lite["log_custom_tiles"][0]["label"] == "Headache"
    assert lite["log_tile_hidden"] == ["vomiting"]
    assert lite["diagnostics"] == []
    assert len(lite["journal"]) == 2
    assert lite["medications"][0]["name"] == "CBD 1 drop"

    full = get_patient_profile(patient_id)
    assert len(full["diagnostics"]) == 1
    assert full["diagnostics"][0]["name"] == "Iron"
