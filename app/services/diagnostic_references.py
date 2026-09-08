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

    # --- Full CBC (SI / Canadian-style) ---
    if key in {"wbc", "white blood cell", "white blood cells", "white blood cell count", "leukocytes"}:
        return _ref(
            low=4.0,
            high=11.0,
            label="Typical 4.0–11.0",
            direction="range",
            note=f"×10⁹/L adult ({who})",
            meaning="White blood cell count reflects immune activity. High values can mean infection or inflammation; low values can mean marrow suppression or other causes.",
            info_url="https://medlineplus.gov/lab-tests/white-blood-count-wbc/",
            info_source="MedlinePlus",
        )
    if key in {"rbc", "red blood cell", "red blood cells", "red blood cell count"}:
        if sex == "female":
            return _ref(
                low=3.8,
                high=5.2,
                label="Typical 3.8–5.2",
                direction="range",
                note=f"×10¹²/L adult female{age_bit}",
                meaning="Red blood cell count measures oxygen-carrying cells. Low counts relate to anemia; high counts can occur with dehydration or polycythemia.",
                info_url="https://medlineplus.gov/lab-tests/red-blood-cell-rbc-count/",
                info_source="MedlinePlus",
            )
        return _ref(
            low=4.2,
            high=5.9,
            label="Typical 4.2–5.9",
            direction="range",
            note=f"×10¹²/L adult male{age_bit}",
            meaning="Red blood cell count measures oxygen-carrying cells. Low counts relate to anemia; high counts can occur with dehydration or polycythemia.",
            info_url="https://medlineplus.gov/lab-tests/red-blood-cell-rbc-count/",
            info_source="MedlinePlus",
        )
    if key in {"hematocrit", "hct", "packed cell volume"}:
        # Canadian labs often report L/L (0.40–0.50); some use %
        unit_l = (unit or "").strip().lower()
        if unit_l in {"%", "percent", "pct"}:
            low, high = (36.0, 46.0) if sex == "female" else (40.0, 52.0)
            label = f"Typical {low:.0f}–{high:.0f}%"
        else:
            low, high = (0.36, 0.46) if sex == "female" else (0.40, 0.52)
            label = f"Typical {low:.2f}–{high:.2f}"
        return _ref(
            low=low,
            high=high,
            label=label,
            direction="range",
            note=f"Adult {who}",
            meaning="Hematocrit is the fraction of blood made of red cells. It moves with hemoglobin and helps characterize anemia or polycythemia.",
            info_url="https://medlineplus.gov/lab-tests/hematocrit-test/",
            info_source="MedlinePlus",
        )
    if key == "mcv":
        return _ref(
            low=80,
            high=100,
            label="Typical 80–100",
            direction="range",
            note="fL adult",
            meaning="Mean corpuscular volume is average red-cell size. Low MCV suggests iron deficiency or thalassemia trait; high MCV can reflect B12/folate deficiency or other causes.",
            info_url="https://medlineplus.gov/lab-tests/mcv-mean-corpuscular-volume/",
            info_source="MedlinePlus",
        )
    if key == "mch":
        return _ref(
            low=27,
            high=33,
            label="Typical 27–33",
            direction="range",
            note="pg adult",
            meaning="Mean corpuscular hemoglobin is average hemoglobin per red cell. It usually tracks with MCV when evaluating anemia.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key == "mchc":
        unit_l = (unit or "").strip().lower()
        if "g/dl" in unit_l or unit_l == "g/dl":
            low, high, label = 32.0, 36.0, "Typical 32–36"
        else:
            # g/L SI
            low, high, label = 320.0, 360.0, "Typical 320–360"
        return _ref(
            low=low,
            high=high,
            label=label,
            direction="range",
            note="Adult",
            meaning="Mean corpuscular hemoglobin concentration reflects hemoglobin concentration inside red cells. Very low or high values help refine anemia type.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key in {"rdw", "rdw-cv", "red cell distribution width"}:
        return _ref(
            low=11.0,
            high=15.0,
            label="Typical 11–15%",
            direction="range",
            note="Adult (lab-specific)",
            meaning="RDW measures variation in red-cell size. Higher RDW often appears with iron deficiency and mixed anemia.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key == "neutrophils":
        return _ref(
            low=2.0,
            high=7.5,
            label="Typical 2.0–7.5",
            direction="range",
            note=f"×10⁹/L adult ({who})",
            meaning="Neutrophils are the main bacteria-fighting white cells. High counts often track infection or stress; low counts raise infection risk.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key == "lymphocytes":
        return _ref(
            low=1.0,
            high=4.0,
            label="Typical 1.0–4.0",
            direction="range",
            note=f"×10⁹/L adult ({who})",
            meaning="Lymphocytes support viral defense and immune memory. Abnormal counts can reflect infection, inflammation, or marrow/immune disorders.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key == "monocytes":
        return _ref(
            low=0.2,
            high=0.8,
            label="Typical 0.2–0.8",
            direction="range",
            note=f"×10⁹/L adult ({who})",
            meaning="Monocytes help clear debris and fight certain infections. Persistently high counts may warrant clinical review.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key == "eosinophils":
        return _ref(
            low=0.0,
            high=0.5,
            label="Typical 0–0.5",
            direction="range",
            note=f"×10⁹/L adult ({who})",
            meaning="Eosinophils rise with allergies, asthma, and some parasitic infections.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key == "basophils":
        return _ref(
            low=0.0,
            high=0.2,
            label="Typical 0–0.2",
            direction="range",
            note=f"×10⁹/L adult ({who})",
            meaning="Basophils are uncommon white cells involved in allergic responses. Mild elevations are often nonspecific.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )
    if key in {
        "immature granulocytes",
        "metamyelocytes",
        "myelocytes",
        "promyelocytes",
        "plasma cells",
        "leukocytes other",
        "lymphocytes variant",
        "nucleated rbc",
    }:
        return _ref(
            low=None,
            high=0.0,
            label="Normally 0",
            direction="lower_better",
            note="Peripheral blood; any sustained presence needs clinical context",
            meaning="Immature or atypical cells are usually absent in healthy adult peripheral blood. Finding them can signal stress, recovery from marrow suppression, or a hematologic process.",
            info_url="https://medlineplus.gov/lab-tests/blood-differential/",
            info_source="MedlinePlus",
        )

    # --- Chem / electrolytes / liver extras ---
    if key == "sodium":
        return _ref(
            low=136,
            high=145,
            label="Typical 136–145",
            direction="range",
            note=f"mmol/L adult ({who})",
            meaning="Sodium is the main blood electrolyte for fluid balance. Low or high values can cause neurologic symptoms and need careful interpretation with volume status.",
            info_url="https://medlineplus.gov/lab-tests/sodium-blood-test/",
            info_source="MedlinePlus",
        )
    if key == "potassium":
        return _ref(
            low=3.5,
            high=5.1,
            label="Typical 3.5–5.1",
            direction="range",
            note=f"mmol/L adult ({who})",
            meaning="Potassium is critical for heart and muscle function. Both low and high values can be dangerous, especially with kidney disease or certain medications.",
            info_url="https://medlineplus.gov/lab-tests/potassium-blood-test/",
            info_source="MedlinePlus",
        )
    if key == "magnesium":
        return _ref(
            low=0.70,
            high=1.00,
            label="Typical 0.70–1.00",
            direction="range",
            note=f"mmol/L adult ({who})",
            meaning="Magnesium supports nerve, muscle, and heart rhythm. Low levels are common with diuretics, GI losses, or poor intake.",
            info_url="https://medlineplus.gov/lab-tests/magnesium-blood-test/",
            info_source="MedlinePlus",
        )
    if key == "albumin":
        return _ref(
            low=35,
            high=50,
            label="Typical 35–50",
            direction="range",
            note=f"g/L adult ({who})",
            meaning="Albumin is the main blood protein made by the liver. Low albumin can reflect liver disease, inflammation, kidney loss, or malnutrition.",
            info_url="https://medlineplus.gov/lab-tests/albumin-blood-albumin-urine/",
            info_source="MedlinePlus",
        )
    if "albumin" in key and "urine" in key:
        return _ref(
            low=None,
            high=30,
            label="Typical <30",
            direction="lower_better",
            note="mg/L spot urine (lab-specific); ACR preferred for kidney screening",
            meaning="Urine albumin can signal early kidney damage, especially with diabetes or hypertension. Spot values need context; albumin/creatinine ratio is often preferred.",
            info_url="https://medlineplus.gov/lab-tests/microalbumin-creatinine-ratio/",
            info_source="MedlinePlus",
        )
    if key in {"alkaline phosphatase", "alp"}:
        return _ref(
            low=40,
            high=129,
            label="Typical 40–129",
            direction="range",
            note=f"U/L adult ({who}); lab-specific",
            meaning="Alkaline phosphatase rises with bile-duct obstruction, some bone conditions, and liver disease.",
            info_url="https://medlineplus.gov/lab-tests/alkaline-phosphatase/",
            info_source="MedlinePlus",
        )
    if key in {"calcium ionized", "ionized calcium"} or key.startswith("calcium ionized"):
        return _ref(
            low=1.15,
            high=1.35,
            label="Typical 1.15–1.35",
            direction="range",
            note=f"mmol/L adult ({who})",
            meaning="Ionized calcium is the active form of calcium in blood. Abnormal values can affect nerves, muscles, and heart rhythm.",
            info_url="https://medlineplus.gov/lab-tests/calcium-blood-test/",
            info_source="MedlinePlus",
        )
    if key in {"ph", "blood ph"}:
        return _ref(
            low=7.35,
            high=7.45,
            label="Typical 7.35–7.45",
            direction="range",
            note="Arterial/venous blood gas context matters",
            meaning="Blood pH reflects acid–base balance. Values outside this narrow band need urgent clinical context (respiratory or metabolic causes).",
            info_url="https://medlineplus.gov/lab-tests/blood-gas-tests/",
            info_source="MedlinePlus",
        )
    if key in {"inr", "pt inr", "prothrombin time inr"}:
        return _ref(
            low=0.8,
            high=1.2,
            label="Typical 0.8–1.2",
            direction="range",
            note="Not on anticoagulants; therapeutic ranges differ on warfarin",
            meaning="INR standardizes clotting time. Higher INR means thinner blood / slower clotting — expected on warfarin, concerning if unexplained.",
            info_url="https://medlineplus.gov/lab-tests/prothrombin-time-test-and-inr-pt-inr/",
            info_source="MedlinePlus",
        )
    if key in {
        "erythrocyte sedimentation rate",
        "esr",
        "sed rate",
    }:
        # Rough adult ESR; rises with age
        if sex == "female":
            high = 20 if (age is None or age < 50) else 30
        else:
            high = 15 if (age is None or age < 50) else 20
        if age is not None and age >= 65:
            high = 30 if sex == "female" else 20
        return _ref(
            low=None,
            high=float(high),
            label=f"Typical <{high}",
            direction="lower_better",
            note=f"mm/hr adult {who}",
            meaning="ESR is a nonspecific inflammation marker. It rises with infection, autoimmune disease, and aging — interpret with symptoms and CRP.",
            info_url="https://medlineplus.gov/lab-tests/erythrocyte-sedimentation-rate-esr/",
            info_source="MedlinePlus",
        )

    # --- Iron studies ---
    if key == "iron":
        if sex == "female":
            return _ref(
                low=10,
                high=30,
                label="Typical 10–30",
                direction="range",
                note=f"µmol/L adult female{age_bit}",
                meaning="Serum iron fluctuates through the day and with meals. Interpret with ferritin, TIBC, and transferrin saturation.",
                info_url="https://medlineplus.gov/lab-tests/serum-iron-test/",
                info_source="MedlinePlus",
            )
        return _ref(
            low=11,
            high=32,
            label="Typical 11–32",
            direction="range",
            note=f"µmol/L adult male{age_bit}",
            meaning="Serum iron fluctuates through the day and with meals. Interpret with ferritin, TIBC, and transferrin saturation.",
            info_url="https://medlineplus.gov/lab-tests/serum-iron-test/",
            info_source="MedlinePlus",
        )
    if key in {"tibc", "total iron binding capacity"}:
        return _ref(
            low=45,
            high=80,
            label="Typical 45–80",
            direction="range",
            note=f"µmol/L adult ({who})",
            meaning="TIBC estimates transferrin capacity to carry iron. High TIBC often accompanies iron deficiency; low TIBC can appear with inflammation or chronic disease.",
            info_url="https://medlineplus.gov/lab-tests/iron-tests/",
            info_source="MedlinePlus",
        )
    if key == "transferrin":
        return _ref(
            low=2.0,
            high=3.6,
            label="Typical 2.0–3.6",
            direction="range",
            note=f"g/L adult ({who})",
            meaning="Transferrin is the iron-transport protein. Levels rise in iron deficiency and fall with inflammation or malnutrition.",
            info_url="https://medlineplus.gov/lab-tests/iron-tests/",
            info_source="MedlinePlus",
        )
    if key in {"transferrin saturation", "iron saturation", "tsat"}:
        # Fraction (0.20–0.50) or percent (20–50)
        unit_l = (unit or "").strip().lower()
        if "%" in unit_l or "percent" in unit_l:
            low, high, label = 20.0, 50.0, "Typical 20–50%"
        else:
            low, high, label = 0.20, 0.50, "Typical 0.20–0.50"
        return _ref(
            low=low,
            high=high,
            label=label,
            direction="range",
            note=f"Adult ({who})",
            meaning="Transferrin saturation is serum iron ÷ TIBC. Low saturation suggests iron deficiency; high saturation can indicate overload.",
            info_url="https://medlineplus.gov/lab-tests/iron-tests/",
            info_source="MedlinePlus",
        )

    # --- Hormones / markers ---
    if key in {"total psa", "psa", "prostate specific antigen"}:
        # Age-banded orientation for males (µg/L); not a biopsy threshold by itself
        if age is not None and age >= 70:
            high, label = 6.5, "Age-oriented <6.5"
        elif age is not None and age >= 60:
            high, label = 4.5, "Age-oriented <4.5"
        elif age is not None and age >= 50:
            high, label = 3.5, "Age-oriented <3.5"
        else:
            high, label = 2.5, "Age-oriented <2.5"
        return _ref(
            low=None,
            high=high,
            label=label,
            direction="lower_better",
            note=f"µg/L male ({who}); trends and free PSA matter more than one cut-off",
            meaning="PSA is a prostate protein in blood. It can rise with enlargement, infection, or cancer — interpret with age, trend, and clinical exam, not a single number.",
            info_url="https://medlineplus.gov/lab-tests/prostate-specific-antigen-psa-test/",
            info_source="MedlinePlus",
        )
    if key == "testosterone":
        # Total testosterone nmol/L — morning adult male orientation
        if sex == "female":
            return _ref(
                low=0.5,
                high=2.5,
                label="Typical 0.5–2.5",
                direction="range",
                note=f"nmol/L adult female{age_bit}; lab-specific",
                meaning="Testosterone in women comes mainly from ovaries and adrenals. Abnormal levels need endocrine context (symptoms, cycle, other hormones).",
                info_url="https://medlineplus.gov/lab-tests/testosterone-levels-test/",
                info_source="MedlinePlus",
            )
        return _ref(
            low=8.0,
            high=30.0,
            label="Typical 8–30",
            direction="range",
            note=f"nmol/L morning adult male{age_bit}; declines with age",
            meaning="Total testosterone supports libido, muscle, bone, and mood in men. Confirm low values with morning repeats and free testosterone / SHBG when needed.",
            info_url="https://medlineplus.gov/lab-tests/testosterone-levels-test/",
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
