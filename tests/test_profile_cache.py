"""Repeated profile reads should not re-enrich labs from disk every time."""

from __future__ import annotations

from app.services import case_manager


def test_profile_cache_hits_same_mtime(tmp_path, monkeypatch):
    patient_id = "cache-patient"
    profile_dir = tmp_path / "patients" / patient_id
    profile_dir.mkdir(parents=True)
    profile_path = profile_dir / "profile.json"
    profile_path.write_text(
        """
        {
          "diagnostics": [
            {"id": "d1", "name": "Iron", "value": 101, "unit": "mcg/dL", "recorded_at": "2026-09-11"}
          ],
          "journal": [],
          "medications": []
        }
        """.strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(case_manager, "_profile_path", lambda _pid: profile_path)
    monkeypatch.setattr(case_manager, "_patient_dir", lambda _pid: profile_dir)
    case_manager._profile_cache_clear()

    first = case_manager.get_patient_profile(patient_id)
    second = case_manager.get_patient_profile(patient_id)
    assert first["diagnostics"][0]["name"] == "Iron"
    assert second["diagnostics"][0]["id"] == first["diagnostics"][0]["id"]
    # Caller mutations must not leak into the cache.
    second["diagnostics"][0]["name"] = "CHANGED"
    third = case_manager.get_patient_profile(patient_id)
    assert third["diagnostics"][0]["name"] == "Iron"

    case_manager.save_patient_profile(
        patient_id,
        {**first, "gender": "female"},
        reload=False,
    )
    again = case_manager.get_patient_profile(patient_id)
    assert again["gender"] == "female"
