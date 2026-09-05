"""Age/gender-aware reference bands for key diagnostic charts.

Ranges are approximate clinical targets / typical lab reference intervals
in SI units commonly used in Canadian reports (mmol/L, g/L, µmol/L, etc.).
They are for orientation on charts — not individualized medical advice.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def age_years_from_dob(date_of_birth: str | None, *, as_of: date | None = None) -> int | None:
    if not date_of_birth:
        return None
    try:
        dob = date.fromisoformat(str(date_of_birth)[:10])
    except ValueError:
        return None
    today = as_of or date.today()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(0, years)


def _norm_gender(gender: str | None) -> str | None:
    g = (gender or "").strip().lower()
    if g in {"m", "male", "man"}:
        return "male"
    if g in {"f", "female", "woman"}:
        return "female"
    return None


def _ref(
    *,
    low: float | None,
    high: float | None,
    label: str,
    direction: str,
    note: str,
    meaning: str,
    info_url: str,
    info_source: str,
) -> dict[str, Any]:
    return {
        "low": low,
        "high": high,
        "label": label,
        "direction": direction,
        "note": note,
        "meaning": meaning,
        "info_url": info_url,
        "info_source": info_source,
    }


def reference_for_metric(
    name: str,
    *,
    unit: str | None = None,
    gender: str | None = None,
    age: int | None = None,
) -> dict[str, Any] | None:
    """Return a chart reference band + education link for a metric."""
    key = (name or "").strip().lower()
    sex = _norm_gender(gender)
    age_bit = f", age {age}" if age is not None else ""
    sex_bit = sex or "adult"
    who = f"{sex_bit}{age_bit}"

    # --- Lipids (mmol/L) ---
    if key in {"ldl cholesterol", "ldl"}:
        return _ref(
            low=None,
            high=2.6,
            label="Desirable <2.6",
            direction="lower_better",
            note=f"General adult target ({who}); tighter if high CV risk",
            meaning="LDL (“bad”) cholesterol contributes to plaque in arteries. Lower is generally better for heart risk.",
            info_url="https://medlineplus.gov/ldlthebadcholesterol.html",
            info_source="MedlinePlus",
        )
    if key in {"non-hdl cholesterol", "non hdl cholesterol"}:
        return _ref(
            low=None,
            high=3.4,
            label="Desirable <3.4",
            direction="lower_better",
            note=f"General adult target ({who})",
            meaning="Non-HDL cholesterol is total cholesterol minus HDL — it captures all atherogenic lipoproteins.",
            info_url="https://medlineplus.gov/cholesterollevelswhatyouneedtoknow.html",
            info_source="MedlinePlus",
        )
    if key in {"total cholesterol", "cholesterol"}:
        return _ref(
            low=None,
            high=5.2,
            label="Desirable <5.2",
            direction="lower_better",
            note=f"Adult ({who})",
            meaning="Total cholesterol is the overall amount of cholesterol in the blood (LDL + HDL + other particles).",
            info_url="https://medlineplus.gov/cholesterollevelswhatyouneedtoknow.html",
            info_source="MedlinePlus",
        )
    if key in {"hdl cholesterol", "hdl"}:
        floor = 1.0 if sex != "female" else 1.3
        return _ref(
            low=floor,
            high=None,
            label=f"Desirable ≥{floor}",
            direction="higher_better",
            note=f"Adult {who}",
            meaning="HDL (“good”) cholesterol helps remove cholesterol from arteries. Higher values are generally better.",
            info_url="https://medlineplus.gov/hdlthegoodcholesterol.html",
            info_source="MedlinePlus",
        )
    if key in {"triglyceride", "triglycerides"}:
        return _ref(
            low=None,
            high=1.7,
            label="Desirable <1.7",
            direction="lower_better",
            note=f"Fasting adult ({who})",
            meaning="Triglycerides are blood fats. High levels raise risk of heart disease and pancreatitis.",
            info_url="https://medlineplus.gov/triglycerides.html",
            info_source="MedlinePlus",
        )
    if key in {"cholesterol/hdl ratio", "chol/hdl ratio", "tc/hdl"}:
        return _ref(
            low=None,
            high=5.0,
            label="Desirable <5.0",
            direction="lower_better",
            note=f"Adult ({who}); optimal often <4.0",
            meaning="The total-to-HDL cholesterol ratio summarizes lipid balance; lower ratios usually mean lower CV risk.",
            info_url="https://www.heart.org/en/health-topics/cholesterol/about-cholesterol/what-your-cholesterol-levels-mean",
            info_source="American Heart Association",
        )

    # --- Glycemic ---
    if key in {"hba1c"}:
        return _ref(
            low=None,
            high=5.7,
            label="Normal <5.7%",
            direction="lower_better",
            note="ADA non-diabetic range",
            meaning="HbA1c reflects average blood sugar over ~3 months. Used to screen for and monitor diabetes.",
            info_url="https://medlineplus.gov/lab-tests/hemoglobin-a1c-hba1c-test/",
            info_source="MedlinePlus",
        )
    if "glucose" in key:
        return _ref(
            low=3.9,
            high=5.5,
            label="Fasting 3.9–5.5",
            direction="range",
            note=f"Fasting adult ({who})",
            meaning="Fasting glucose is the blood sugar level after not eating. High values can indicate prediabetes or diabetes.",
            info_url="https://medlineplus.gov/lab-tests/blood-glucose-test/",
            info_source="MedlinePlus",
        )

    # --- Kidney / liver ---
    if key == "creatinine":
        if sex == "female":
            return _ref(
                low=45,
                high=90,
                label="Typical 45–90",
                direction="range",
                note=f"Adult female{age_bit}",
                meaning="Creatinine is a waste product filtered by the kidneys. High levels can signal reduced kidney function.",
                info_url="https://medlineplus.gov/lab-tests/creatinine-test/",
                info_source="MedlinePlus",
            )
        return _ref(
            low=60,
            high=110,
            label="Typical 60–110",
            direction="range",
            note=f"Adult male{age_bit}",
            meaning="Creatinine is a waste product filtered by the kidneys. High levels can signal reduced kidney function.",
            info_url="https://medlineplus.gov/lab-tests/creatinine-test/",
            info_source="MedlinePlus",
        )
    if key == "egfr":
        return _ref(
            low=60,
            high=None,
            label="Normal ≥60",
            direction="higher_better",
            note=f"Adult ({who}); ≥90 preferred",
            meaning="eGFR estimates how well the kidneys filter blood. Lower values suggest reduced kidney function.",
            info_url="https://medlineplus.gov/lab-tests/glomerular-filtration-rate-gfr-test/",
            info_source="MedlinePlus",
        )
    if key == "alt":
        high = 35 if sex == "female" else 50
        return _ref(
            low=None,
            high=high,
            label=f"Typical <{high}",
            direction="lower_better",
            note=f"Adult {who}",
            meaning="ALT is a liver enzyme. Elevated ALT can indicate liver inflammation or injury.",
            info_url="https://medlineplus.gov/lab-tests/alanine-transaminase-alt-test/",
            info_source="MedlinePlus",
        )
    if key == "ast":
        high = 30 if sex == "female" else 40
        return _ref(
            low=None,
            high=high,
            label=f"Typical <{high}",
            direction="lower_better",
            note=f"Adult {who}",
            meaning="AST is an enzyme found in liver and muscle. High values may reflect liver or other tissue injury.",
            info_url="https://medlineplus.gov/lab-tests/aspartate-aminotransferase-ast-test/",
            info_source="MedlinePlus",
        )
    if "bilirubin" in key:
        return _ref(
            low=None,
            high=20,
            label="Typical <20",
            direction="lower_better",
            note="Adult",
            meaning="Bilirubin comes from breakdown of red blood cells. High levels can cause jaundice and may reflect liver or bile-duct issues.",
            info_url="https://medlineplus.gov/lab-tests/bilirubin-blood-test/",
            info_source="MedlinePlus",
        )

    # --- CBC / inflammation / hormones ---
    if key == "hemoglobin":
        if sex == "female":
            return _ref(
                low=120,
                high=160,
                label="Typical 120–160",
                direction="range",
                note=f"Adult female{age_bit}",
                meaning="Hemoglobin carries oxygen in red blood cells. Low values suggest anemia; high values can occur with smoking or other conditions.",
                info_url="https://medlineplus.gov/lab-tests/hemoglobin-test/",
                info_source="MedlinePlus",
            )
        return _ref(
            low=130,
            high=170,
            label="Typical 130–170",
            direction="range",
            note=f"Adult male{age_bit}",
            meaning="Hemoglobin carries oxygen in red blood cells. Low values suggest anemia; high values can occur with smoking or other conditions.",
            info_url="https://medlineplus.gov/lab-tests/hemoglobin-test/",
            info_source="MedlinePlus",
        )
    if key == "platelets":
        return _ref(
            low=150,
            high=400,
            label="Typical 150–400",
            direction="range",
            note="Adult",
            meaning="Platelets help blood clot. Low counts raise bleeding risk; high counts can increase clotting risk.",
            info_url="https://medlineplus.gov/lab-tests/platelet-count/",
            info_source="MedlinePlus",
        )
    if key == "crp":
        return _ref(
            low=None,
            high=5.0,
            label="Typical <5",
            direction="lower_better",
            note="hs-CRP CV risk often uses <1 / 1–3 / >3",
            meaning="CRP rises with inflammation. High-sensitivity CRP is also used as a cardiovascular risk marker.",
            info_url="https://medlineplus.gov/lab-tests/c-reactive-protein-crp-test/",
            info_source="MedlinePlus",
        )
    if key == "tsh":
        return _ref(
            low=0.4,
            high=4.0,
            label="Typical 0.4–4.0",
            direction="range",
            note="Adult (lab-specific)",
            meaning="TSH is the pituitary signal that drives the thyroid. Abnormal TSH often points to under- or over-active thyroid.",
            info_url="https://medlineplus.gov/lab-tests/tsh-thyroid-stimulating-hormone-test/",
            info_source="MedlinePlus",
        )
    if "vitamin d" in key:
        return _ref(
            low=75,
            high=250,
            label="Adequate 75–250",
            direction="range",
            note="nmol/L; insufficiency often <75",
            meaning="Vitamin D supports bone health and other systems. Low levels are common and may need diet, sun, or supplements.",
            info_url="https://medlineplus.gov/vitaminddeficiency.html",
            info_source="MedlinePlus",
        )
    if "b12" in key or "vitamin b12" in key:
        return _ref(
            low=150,
            high=None,
            label="Typical ≥150",
            direction="higher_better",
            note="pmol/L; lab-specific",
            meaning="Vitamin B12 is needed for nerves and red blood cells. Low levels can cause anemia and neurologic symptoms.",
            info_url="https://medlineplus.gov/vitaminb12.html",
            info_source="MedlinePlus",
        )
    if key == "ferritin":
        if sex == "female":
            return _ref(
                low=15,
                high=150,
                label="Typical 15–150",
                direction="range",
                note=f"Adult female{age_bit}",
                meaning="Ferritin reflects iron stores. Low ferritin suggests iron deficiency; very high values can indicate inflammation or overload.",
                info_url="https://medlineplus.gov/lab-tests/ferritin-blood-test/",
                info_source="MedlinePlus",
            )
        return _ref(
            low=30,
            high=400,
            label="Typical 30–400",
            direction="range",
            note=f"Adult male{age_bit}",
            meaning="Ferritin reflects iron stores. Low ferritin suggests iron deficiency; very high values can indicate inflammation or overload.",
            info_url="https://medlineplus.gov/lab-tests/ferritin-blood-test/",
            info_source="MedlinePlus",
        )

    # --- Imaging / vitals ---
    if "calcium score" in key or "agatston" in key:
        return _ref(
            low=None,
            high=100,
            label="Mild <100 (0 ideal)",
            direction="lower_better",
            note=f"Agatston units; interpret with age/sex percentile ({who})",
            meaning="Coronary calcium score measures calcified plaque in heart arteries. Higher scores generally mean higher atherosclerotic burden.",
            info_url="https://www.heart.org/en/health-topics/heart-attack/diagnosing-a-heart-attack/coronary-calcium-scan-heart-scan",
            info_source="American Heart Association",
        )
    if "systolic" in key:
        return _ref(
            low=None,
            high=120,
            label="Optimal <120",
            direction="lower_better",
            note=f"Adult ({who})",
            meaning="Systolic blood pressure is the top number — pressure when the heart contracts.",
            info_url="https://medlineplus.gov/highbloodpressure.html",
            info_source="MedlinePlus",
        )
    if "diastolic" in key:
        return _ref(
            low=None,
            high=80,
            label="Optimal <80",
            direction="lower_better",
            note=f"Adult ({who})",
            meaning="Diastolic blood pressure is the bottom number — pressure when the heart relaxes between beats.",
            info_url="https://medlineplus.gov/highbloodpressure.html",
            info_source="MedlinePlus",
        )
    if key == "bmi":
        return _ref(
            low=18.5,
            high=24.9,
            label="Healthy 18.5–24.9",
            direction="range",
            note="WHO adult BMI",
            meaning="Body mass index relates weight to height. It is a screening tool, not a complete health measure.",
            info_url="https://medlineplus.gov/ency/article/007196.htm",
            info_source="MedlinePlus",
        )
    if key == "weight":
        return None

    return None


def status_for_value(value: float | None, reference: dict[str, Any] | None) -> str | None:
    """Return green | yellow | red relative to the reference band.

    Green: on-target.
    Yellow: within 10% beyond the relevant bound.
    Red: farther than 10% beyond the bound.
    """
    if value is None or reference is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None

    low = reference.get("low")
    high = reference.get("high")
    direction = reference.get("direction") or "range"
    try:
        low_f = float(low) if low is not None else None
    except (TypeError, ValueError):
        low_f = None
    try:
        high_f = float(high) if high is not None else None
    except (TypeError, ValueError):
        high_f = None

    def band(distance: float, threshold: float) -> str:
        if threshold == 0:
            return "yellow" if abs(distance) > 0 else "green"
        pct = abs(distance) / abs(threshold)
        if pct <= 1e-9:
            return "green"
        if pct <= 0.10:
            return "yellow"
        return "red"

    if direction == "lower_better" and high_f is not None:
        if v <= high_f:
            return "green"
        return band(v - high_f, high_f)

    if direction == "higher_better" and low_f is not None:
        if v >= low_f:
            return "green"
        return band(low_f - v, low_f)

    # Range (or fallback when both bounds exist)
    if low_f is not None and high_f is not None:
        if low_f <= v <= high_f:
            return "green"
        if v < low_f:
            return band(low_f - v, low_f)
        return band(v - high_f, high_f)
    if high_f is not None:
        if v <= high_f:
            return "green"
        return band(v - high_f, high_f)
    if low_f is not None:
        if v >= low_f:
            return "green"
        return band(low_f - v, low_f)
    return None


def attach_references_to_series(
    series: list[dict[str, Any]],
    *,
    date_of_birth: str | None,
    gender: str | None,
) -> list[dict[str, Any]]:
    age = age_years_from_dob(date_of_birth)
    out: list[dict[str, Any]] = []
    for item in series:
        row = dict(item)
        ref = reference_for_metric(
            str(row.get("name") or ""),
            unit=row.get("unit"),
            gender=gender,
            age=age,
        )
        if ref:
            row["reference"] = ref
            latest = row.get("latest") or {}
            status = status_for_value(latest.get("value"), ref)
            if status:
                row["status"] = status
            # Per-reading status for chart coloring
            readings = []
            for r in row.get("readings") or []:
                rr = dict(r)
                st = status_for_value(rr.get("value"), ref)
                if st:
                    rr["status"] = st
                readings.append(rr)
            row["readings"] = readings
        out.append(row)
    return out
