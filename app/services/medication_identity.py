"""Identify medication names against a local known-drug dictionary."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

KNOWN_MEDS_PATH = Path(__file__).resolve().parent.parent / "data" / "known_medications.json"

# Exact / near-exact
KNOWN_THRESHOLD = 0.97
UNCERTAIN_THRESHOLD = 0.86


def normalize_med_name(name: str | None) -> str:
    text = str(name or "").lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^\w\s\-/+]", " ", text)
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip()
    # Drop common dose tokens that sometimes ride along in the name field
    text = re.sub(
        r"\b\d+(\.\d+)?\s*(mg|mcg|µg|g|ml|iu|units?|%|tab|tabs|cap|caps)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _known_map() -> dict[str, str]:
    if not KNOWN_MEDS_PATH.exists():
        return {}
    try:
        data = json.loads(KNOWN_MEDS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("names") if isinstance(data, dict) else data
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, display in raw.items():
        nk = normalize_med_name(str(key))
        if nk:
            out[nk] = str(display or key)
    return out


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if len(shorter) >= 4 and len(shorter) / max(len(longer), 1) >= 0.55:
            return max(0.9, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def identify_medication(name: str | None) -> dict[str, Any]:
    """Return identity status for a medication name."""
    raw = " ".join(str(name or "").strip().split())
    norm = normalize_med_name(raw)
    if not norm:
        return {
            "status": "unknown",
            "matched_name": None,
            "score": 0.0,
            "normalized": "",
        }

    known = _known_map()
    if norm in known:
        return {
            "status": "known",
            "matched_name": known[norm],
            "score": 1.0,
            "normalized": norm,
        }

    best_key = None
    best_score = 0.0
    # Prefer candidates sharing a token prefix to keep lookups cheap
    tokens = set(norm.split())
    for key in known:
        if tokens and not (tokens & set(key.split())):
            # Still allow short single-token fuzzy (brand typos)
            if len(norm) < 5 or len(key) < 5:
                continue
            if abs(len(norm) - len(key)) > max(4, len(norm) // 2):
                continue
        score = _similarity(norm, key)
        if score > best_score:
            best_score = score
            best_key = key

    if best_key is not None and best_score >= KNOWN_THRESHOLD:
        return {
            "status": "known",
            "matched_name": known[best_key],
            "score": round(best_score, 3),
            "normalized": norm,
        }
    if best_key is not None and best_score >= UNCERTAIN_THRESHOLD:
        return {
            "status": "uncertain",
            "matched_name": known[best_key],
            "score": round(best_score, 3),
            "normalized": norm,
        }
    return {
        "status": "unknown",
        "matched_name": known.get(best_key) if best_key and best_score >= 0.7 else None,
        "score": round(best_score, 3),
        "normalized": norm,
    }


def apply_identity_fields(med: dict[str, Any]) -> dict[str, Any]:
    """Attach identity_* fields onto a medication dict (mutates and returns)."""
    identity = identify_medication(med.get("name"))
    med["identity_status"] = identity["status"]
    med["identity_match"] = identity.get("matched_name")
    med["identity_score"] = identity.get("score")
    return med


def annotate_medications(medications: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in medications or []:
        if not isinstance(raw, dict):
            continue
        med = dict(raw)
        apply_identity_fields(med)
        out.append(med)
    return out
