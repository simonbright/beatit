"""Medication health oversight: interactions + dosage review via LLM."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.case_manager import (
    age_years_from_dob,
    get_patient_profile,
    latest_measurement,
    save_patient_profile,
)
from app.services.llm import LLMClient
from app.services.medication_identity import annotate_medications, identify_medication

SAFETY_DISCLAIMER = (
    "Research support only — not a substitute for a pharmacist or clinician review. "
    "Verify interactions and dosing with your care team before changing any medication."
)

SAFETY_SYSTEM = (
    "You are a careful clinical research assistant performing medication health oversight. "
    "You flag possible drug–drug interactions and possible incorrect dosages "
    "(too high or too low for adult outpatient use, given age/weight when available). "
    "You also note unidentified drug names. "
    "Return ONLY a JSON object. No markdown fences, no prose outside JSON. "
    "Be conservative: only flag reasonably supported concerns. "
    "If nothing concerning is found, set all_clear true and say so clearly in overall_summary."
)

SAFETY_USER_TEMPLATE = """Patient context:
{patient_block}

Active medications:
{active_block}

Recently stopped medications:
{stopped_block}

Ad hoc / as-needed medications from self-reports (last 30 days), e.g. pain meds:
{adhoc_block}

Unidentified or uncertain names (local dictionary):
{unidentified_block}

Return JSON with this exact shape:
{{
  "interactions": [
    {{
      "drugs": ["Drug A", "Drug B"],
      "severity": "high|moderate|low",
      "summary": "what the interaction may be",
      "advice": "what to discuss with care team"
    }}
  ],
  "dosage_concerns": [
    {{
      "drug": "Drug name",
      "issue": "high|low|unclear",
      "summary": "why the dose may be off",
      "advice": "what to verify"
    }}
  ],
  "unidentified": ["name if not a recognized medication"],
  "all_clear": true,
  "overall_summary": "2-4 sentence overview for health oversight"
}}

Rules:
- Include ad hoc journal meds when assessing interactions.
- If dose/frequency is missing, prefer issue "unclear" only when clinically relevant — otherwise omit.
- all_clear must be true only when interactions and dosage_concerns are both empty (unidentified may still be listed).
- If all_clear is true, overall_summary must explicitly state that no concerning interactions or dosage issues were identified from the available list.
- Do not invent medications. Do not recommend stopping drugs unilaterally.
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_json_object(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _clamp_str(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    return text[:max_len]


def _format_med_line(m: dict[str, Any]) -> str:
    bits = [str(m.get("name") or "?")]
    if m.get("dosage"):
        bits.append(str(m["dosage"]))
    if m.get("frequency"):
        bits.append(str(m["frequency"]))
    if m.get("conditions"):
        bits.append("for " + ", ".join(str(c) for c in m["conditions"]))
    status = m.get("identity_status") or identify_medication(m.get("name")).get("status")
    if status and status != "known":
        match = m.get("identity_match")
        bits.append(f"identity:{status}" + (f"~{match}" if match else ""))
    return " · ".join(bits)


def _patient_block(profile: dict[str, Any]) -> str:
    lines: list[str] = []
    dob = profile.get("date_of_birth")
    age = age_years_from_dob(dob)
    if age is not None:
        lines.append(f"Age: {age}")
    if profile.get("gender"):
        lines.append(f"Gender: {profile['gender']}")
    latest = latest_measurement(profile)
    if latest:
        if latest.get("weight_kg") is not None:
            lines.append(f"Weight: {latest['weight_kg']} kg (as of {latest.get('recorded_at')})")
        if latest.get("height_cm") is not None:
            lines.append(f"Height: {latest['height_cm']} cm")
    return "\n".join(lines) if lines else "No demographics on file."


def _journal_adhoc_meds(profile: dict[str, Any], *, days: int = 30) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []
    for row in profile.get("journal") or []:
        if (row.get("kind") or "") != "medication":
            continue
        raw_ts = str(row.get("recorded_at") or row.get("created_at") or "")
        try:
            ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
        except ValueError:
            pass
        label = str(row.get("label") or row.get("text") or "medication").strip()
        identity = identify_medication(label)
        out.append(
            {
                "name": label,
                "text": row.get("text"),
                "recorded_at": raw_ts,
                "identity_status": identity["status"],
                "identity_match": identity.get("matched_name"),
            }
        )
    return out


def _normalize_result(raw: dict[str, Any], unidentified_local: list[str]) -> dict[str, Any]:
    interactions: list[dict[str, Any]] = []
    for item in raw.get("interactions") or []:
        if not isinstance(item, dict):
            continue
        drugs = item.get("drugs") or []
        if isinstance(drugs, str):
            drugs = [drugs]
        drugs_clean = [d for d in (_clamp_str(x, 80) for x in drugs) if d][:6]
        severity = str(item.get("severity") or "moderate").lower()
        if severity not in {"high", "moderate", "low"}:
            severity = "moderate"
        summary = _clamp_str(item.get("summary"), 400)
        if not drugs_clean or not summary:
            continue
        interactions.append(
            {
                "drugs": drugs_clean,
                "severity": severity,
                "summary": summary,
                "advice": _clamp_str(item.get("advice"), 400),
            }
        )

    dosage_concerns: list[dict[str, Any]] = []
    for item in raw.get("dosage_concerns") or []:
        if not isinstance(item, dict):
            continue
        drug = _clamp_str(item.get("drug"), 120)
        issue = str(item.get("issue") or "unclear").lower()
        if issue not in {"high", "low", "unclear"}:
            issue = "unclear"
        summary = _clamp_str(item.get("summary"), 400)
        if not drug or not summary:
            continue
        dosage_concerns.append(
            {
                "drug": drug,
                "issue": issue,
                "summary": summary,
                "advice": _clamp_str(item.get("advice"), 400),
            }
        )

    unidentified: list[str] = []
    seen: set[str] = set()
    for name in list(raw.get("unidentified") or []) + unidentified_local:
        cleaned = _clamp_str(name, 120)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        unidentified.append(cleaned)

    all_clear = bool(raw.get("all_clear")) and not interactions and not dosage_concerns
    overall = _clamp_str(raw.get("overall_summary"), 800)
    if all_clear and not overall:
        overall = (
            "No concerning drug–drug interactions or dosage issues were identified "
            "from the available medication list and recent self-reported meds."
        )
    if all_clear and overall and "no concern" not in overall.lower() and "all clear" not in overall.lower() and "not identified" not in overall.lower() and "nothing concerning" not in overall.lower():
        overall = (
            "No concerning interactions or dosage issues were identified from the available list. "
            + overall
        )

    return {
        "interactions": interactions[:20],
        "dosage_concerns": dosage_concerns[:20],
        "unidentified": unidentified[:30],
        "all_clear": all_clear,
        "overall_summary": overall
        or "Review completed; see flagged items below.",
        "disclaimer": SAFETY_DISCLAIMER,
    }


def get_medication_safety(patient_id: str) -> dict[str, Any] | None:
    profile = get_patient_profile(patient_id)
    raw = profile.get("medication_safety")
    return raw if isinstance(raw, dict) else None


async def run_medication_safety_review(
    patient_id: str,
    *,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    profile = get_patient_profile(patient_id)
    meds = annotate_medications(profile.get("medications") or [])
    active = [m for m in meds if (m.get("status") or "active") == "active"]
    stopped = [m for m in meds if (m.get("status") or "") == "stopped"][:12]
    adhoc = _journal_adhoc_meds(profile, days=30)

    unidentified_local: list[str] = []
    for m in active + adhoc:
        status = m.get("identity_status") or "unknown"
        if status != "known":
            unidentified_local.append(str(m.get("name") or ""))

    active_block = "\n".join(f"- {_format_med_line(m)}" for m in active) or "- (none)"
    stopped_block = "\n".join(f"- {_format_med_line(m)}" for m in stopped) or "- (none)"
    adhoc_block = (
        "\n".join(
            f"- {a.get('name')} ({a.get('recorded_at', '?')[:16]})"
            + (f" · {a['text']}" if a.get("text") else "")
            + (f" · identity:{a.get('identity_status')}" if a.get("identity_status") != "known" else "")
            for a in adhoc
        )
        or "- (none)"
    )
    unidentified_block = (
        "\n".join(f"- {n}" for n in unidentified_local) if unidentified_local else "- (none)"
    )

    client = llm or LLMClient()
    try:
        raw_text = await client.chat(
            messages=[
                {"role": "system", "content": SAFETY_SYSTEM},
                {
                    "role": "user",
                    "content": SAFETY_USER_TEMPLATE.format(
                        patient_block=_patient_block(profile),
                        active_block=active_block,
                        stopped_block=stopped_block,
                        adhoc_block=adhoc_block,
                        unidentified_block=unidentified_block,
                    ),
                },
            ],
            temperature=0.15,
        )
    except Exception as exc:
        raise ValueError(f"Could not run medication safety review: {exc}") from exc

    payload = _strip_json_object(raw_text)
    try:
        parsed = json.loads(payload) if payload else {}
    except json.JSONDecodeError as exc:
        raise ValueError("Model returned invalid safety-review JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Model returned invalid safety-review JSON")

    result = _normalize_result(parsed, unidentified_local)
    result["active_count"] = len(active)
    result["adhoc_count"] = len(adhoc)
    result["annotated_medications"] = [
        {
            "id": m.get("id"),
            "name": m.get("name"),
            "identity_status": m.get("identity_status"),
            "identity_match": m.get("identity_match"),
        }
        for m in active
    ]

    saved = {
        "ran_at": _now_iso(),
        "result": result,
    }
    profile["medication_safety"] = saved
    # Keep identity fields on meds for display consistency
    profile["medications"] = meds
    save_patient_profile(patient_id, profile)
    return saved
