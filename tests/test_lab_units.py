"""Tests for Canadian SI ↔ US conventional lab unit conversion."""

from __future__ import annotations

from app.services.lab_units import (
    convert_reference_band,
    convert_value,
    enrich_diagnostic_units,
    normalize_unit,
)


def test_normalize_unit_aliases():
    assert normalize_unit("mg/dl") == "mg/dL"
    assert normalize_unit("umol/L") == "umol/L"
    assert normalize_unit("Thousand/uL") == "Thousand/uL"
    assert normalize_unit("x10E9/L") == "x E9/L"


def test_quest_ldl_to_si():
    row = enrich_diagnostic_units(
        {"name": "LDL cholesterol", "value": 207, "unit": "mg/dL"}
    )
    assert row["unit_system_original"] == "us"
    assert row["unit_si"] == "mmol/L"
    assert abs(row["value_si"] - 5.35) < 0.02
    assert row["value_us"] == 207
    assert row["unit_us"] == "mg/dL"


def test_lifelabs_ldl_to_us():
    row = enrich_diagnostic_units(
        {"name": "LDL cholesterol", "value": 2.6, "unit": "mmol/L"}
    )
    assert row["unit_system_original"] == "si"
    assert abs(row["value_us"] - 100.5) < 2
    assert row["unit_us"] == "mg/dL"


def test_glucose_hemoglobin_creatinine():
    glu = enrich_diagnostic_units(
        {"name": "Glucose fasting", "value": 97, "unit": "mg/dL"}
    )
    assert abs(glu["value_si"] - 5.38) < 0.05

    hb = enrich_diagnostic_units({"name": "Hemoglobin", "value": 15.1, "unit": "g/dL"})
    assert abs(hb["value_si"] - 151) < 1

    cr = enrich_diagnostic_units({"name": "Creatinine", "value": 0.78, "unit": "mg/dL"})
    assert abs(cr["value_si"] - 69) < 2


def test_triglyceride_factor_differs_from_cholesterol():
    trig = convert_value("Triglyceride", 150, "mg/dL", "si")
    chol = convert_value("Total cholesterol", 150, "mg/dL", "si")
    assert trig and chol
    assert abs(trig[0] - chol[0]) > 0.5


def test_hba1c_identity():
    row = enrich_diagnostic_units({"name": "HbA1c", "value": 5.6, "unit": "%"})
    assert row["unit_system_original"] == "same"
    assert row["value_si"] == 5.6
    assert row["value_us"] == 5.6


def test_blocked_coronary_calcium_not_converted_as_lipid():
    row = enrich_diagnostic_units(
        {"name": "Coronary calcium score", "value": 40, "unit": "mg/dL"}
    )
    assert row["unit_system_original"] == "unknown"
    assert row["value_si"] == 40
    assert row["value_us"] == 40


def test_cell_counts_one_to_one():
    row = enrich_diagnostic_units(
        {"name": "WBC", "value": 6.2, "unit": "Thousand/uL"}
    )
    assert row["value_si"] == 6.2
    assert row["unit_si"] == "x E9/L"
    assert row["unit_us"] == "Thousand/uL"


def test_convert_reference_band_to_us():
    ref = {
        "low": None,
        "high": 2.6,
        "label": "Desirable <2.6",
        "direction": "lower_better",
        "note": "General adult target",
        "meaning": "LDL",
        "info_url": "https://example.com",
        "info_source": "test",
    }
    us = convert_reference_band("LDL cholesterol", ref, to_system="us")
    assert us is not None
    assert abs(us["high"] - 100.5) < 2
    assert "mg/dL" in us["label"]


def test_iron_mcg_dl_converts_to_si():
    assert normalize_unit("mcg/dL") == "ug/dL"
    row = enrich_diagnostic_units({"name": "Iron", "value": 101, "unit": "mcg/dL"})
    assert row["unit_system_original"] == "us"
    assert row["unit_us"] == "µg/dL" or row["unit_us"] == "ug/dL"
    assert abs(row["value_si"] - (101 / 5.587)) < 0.05
    assert row["unit_si"] in {"µmol/L", "umol/L"}
    # 101 mcg/dL ≈ 18 µmol/L — within male typical 11–32
    assert 11 <= row["value_si"] <= 32


def test_iron_reference_band_converts_units_in_note():
    ref = {
        "low": 11,
        "high": 32,
        "label": "Typical 11–32",
        "direction": "range",
        "note": "µmol/L adult male, age 51",
        "meaning": "Serum iron",
        "info_url": "https://example.com",
        "info_source": "test",
    }
    us = convert_reference_band("Iron", ref, to_system="us")
    assert us is not None
    assert abs(us["low"] - 11 * 5.587) < 1
    assert abs(us["high"] - 32 * 5.587) < 1
    assert "µmol/L" not in us["note"]
    assert "ug/dL" in us["note"].lower() or "µg/dL" in us["note"]


def test_iron_mcg_dl_reenriches_stale_dual_units():
    stale = {
        "name": "Iron",
        "value": 101,
        "unit": "mcg/dL",
        "unit_system_original": "unknown",
        "value_si": 101.0,
        "unit_si": "mcg/dL",
        "value_us": 101.0,
        "unit_us": "mcg/dL",
    }
    row = enrich_diagnostic_units(stale)
    assert abs(row["value_si"] - (101 / 5.587)) < 0.05
    assert row["unit_si"] in {"µmol/L", "umol/L"}
