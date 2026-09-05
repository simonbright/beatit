"""Patient / reviewer context defaults for analysis prompts.

CRITICAL: Never put disease-specific narrative in DEFAULT_PATIENT_CONTEXT.
That string is seeded into every new case DB and was previously a pancreatic-cancer
template, which caused wrong-patient assessments (e.g. Simon labeled with Susan's malignancy).
"""

from __future__ import annotations

DEFAULT_REVIEWER_CONTEXT = """Role: Clinical reviewer synthesizing multidisciplinary records for care planning.

Synthesis standards:
- Read and integrate ALL selected documents before writing — pathology, radiology reports, labs, transcripts, vision reads, PDFs, and clinical notes.
- Do not summarize only the first documents in the file list; cross-check every source in scope.
- Surface every clinically important finding, date, result, contradiction, and recommendation from stored records.
- Distinguish documented facts from inference and from unknowns.
- Prioritize formal reports and AI vision reads over DICOM slice metadata (pixels are not in the chart text).
- Infer specialty from the active case label and patient context; do not assume oncology unless the records support it."""

DEFAULT_PATIENT_CONTEXT = """(No case context set yet.)
Add a short, accurate clinical summary for THIS person and case under Settings → Analysis.
Do not invent diagnoses. Prefer documented facts from the library."""

# Historical seed text that must be scrubbed from any case that still has it.
LEGACY_DEFAULT_PATIENT_CONTEXT = """Woman in her 70s
Recently diagnosed with pancreatic cancer
Possible liver metastasis (staging and extent not yet fully confirmed)
Line of therapy: not yet started systemic treatment (update when known)
Known biomarkers: unknown / pending (e.g. BRCA, KRAS G12C — update when known)
ECOG performance status: unknown (update when known)
Prior therapies: none documented (update when known)"""

LEGACY_DEFAULT_REVIEWER_CONTEXT_PREFIX = "Role: Medical oncologist leading multidisciplinary case review"


def is_legacy_cancer_patient_context(value: str | None) -> bool:
    """True when stored context is the old Susan/pancreatic default (or a near copy)."""
    text = (value or "").strip()
    if not text:
        return False
    legacy = LEGACY_DEFAULT_PATIENT_CONTEXT.strip()
    if text == legacy:
        return True
    # Tolerate whitespace drift
    compact = " ".join(text.split())
    legacy_compact = " ".join(legacy.split())
    if compact == legacy_compact:
        return True
    # Strong fingerprint: default disease lines without personalization
    needles = (
        "recently diagnosed with pancreatic cancer",
        "possible liver metastasis",
        "woman in her 70s",
    )
    lower = compact.lower()
    return all(n in lower for n in needles) and len(compact) < len(legacy_compact) + 80


def is_legacy_oncology_reviewer_context(value: str | None) -> bool:
    text = (value or "").strip()
    return text.startswith(LEGACY_DEFAULT_REVIEWER_CONTEXT_PREFIX)


def case_implies_oncology(*, case_label: str | None = None, case_id: str | None = None) -> bool:
    """True when the case itself is oncology — legacy cancer defaults may be intentional."""
    blob = f"{case_label or ''} {case_id or ''}".lower()
    return any(
        hint in blob
        for hint in (
            "cancer",
            "oncolog",
            "pancrea",
            "tumor",
            "tumour",
            "malignan",
            "chemo",
            "metast",
        )
    )


def should_scrub_legacy_cancer_patient_context(
    value: str | None,
    *,
    case_label: str | None = None,
    case_id: str | None = None,
) -> bool:
    """Scrub Susan's pancreatic seed when it was copied onto a non-oncology case."""
    if not is_legacy_cancer_patient_context(value):
        return False
    return not case_implies_oncology(case_label=case_label, case_id=case_id)


def should_scrub_legacy_oncology_reviewer_context(
    value: str | None,
    *,
    case_label: str | None = None,
    case_id: str | None = None,
) -> bool:
    """Replace the old medical-oncologist seed with the general clinical default.

    Specialty comes from the case and patient context as needed — not from a
    global oncology persona default.
    """
    _ = (case_label, case_id)
    return is_legacy_oncology_reviewer_context(value)
