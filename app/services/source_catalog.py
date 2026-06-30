"""Source type catalog — classification, shorthands, colors, and display names."""

import json
import re
from copy import deepcopy
from typing import Any

DOCUMENT_LABEL_RE = re.compile(r'^document\s+"([^"]+)"\s*$', re.IGNORECASE)

SOURCE_TYPE_KEYS = (
    "document",
    "diagnostic",
    "web",
    "patient_context",
    "inference",
    "unknown",
)

DEFAULT_SOURCE_TYPES: dict[str, dict[str, str]] = {
    "document": {
        "display": "Clinical record",
        "shorthand": "Doc",
        "css_class": "source-document",
        "description": "Hard data from stored records",
    },
    "diagnostic": {
        "display": "Diagnostic test",
        "shorthand": "Diag",
        "css_class": "source-diagnostic",
        "description": "Imaging, pathology, and other diagnostic files",
    },
    "web": {
        "display": "Web source",
        "shorthand": "Web",
        "css_class": "source-web",
        "description": "Content ingested from URLs or transcripts",
    },
    "patient_context": {
        "display": "Patient context",
        "shorthand": "Ctx",
        "css_class": "source-context",
        "description": "From settings — not verified clinical record",
    },
    "inference": {
        "display": "AI inference",
        "shorthand": "AI",
        "css_class": "source-inference",
        "description": "Interpretation — not verified",
    },
    "unknown": {
        "display": "Not documented",
        "shorthand": "?",
        "css_class": "source-unknown",
        "description": "Not supported by stored records — do not treat as fact",
    },
}

DOCUMENT_SOURCE_TYPE_MAP: dict[str, str] = {
    "imaging": "diagnostic",
    "pdf": "document",
    "text": "document",
    "url": "web",
    "youtube": "web",
    "video": "web",
}


def parse_source_labels_json(raw: str | None) -> dict[str, dict[str, str]]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if key not in DEFAULT_SOURCE_TYPES or not isinstance(value, dict):
            continue
        entry: dict[str, str] = {}
        if display := str(value.get("display", "")).strip():
            entry["display"] = display[:120]
        if shorthand := str(value.get("shorthand", "")).strip():
            entry["shorthand"] = shorthand[:12]
        if entry:
            cleaned[key] = entry
    return cleaned


def merge_source_types(custom: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    merged = deepcopy(DEFAULT_SOURCE_TYPES)
    for key, overrides in (custom or {}).items():
        if key not in merged or not isinstance(overrides, dict):
            continue
        for field in ("display", "shorthand"):
            if overrides.get(field):
                merged[key][field] = overrides[field]
    return merged


def document_source_type_key(source_type: str | None) -> str:
    return DOCUMENT_SOURCE_TYPE_MAP.get(str(source_type or "").lower(), "document")


def parse_document_title(label: str) -> str | None:
    match = DOCUMENT_LABEL_RE.match(label.strip())
    if match:
        return match.group(1).strip()
    lower = label.strip().casefold()
    if lower.startswith("document "):
        return label.strip()[9:].strip().strip('"')
    return None


class SourceCatalog:
    def __init__(
        self,
        documents: list[dict[str, Any]] | None = None,
        custom_labels: dict[str, dict[str, str]] | None = None,
    ):
        self.type_defs = merge_source_types(custom_labels)
        self.doc_index: dict[str, dict[str, Any]] = {}
        for doc in documents or []:
            title = doc.get("title")
            if title:
                self.doc_index[str(title).casefold()] = doc

    @classmethod
    def from_settings(
        cls,
        documents: list[dict[str, Any]] | None,
        source_labels_json: str | None,
    ) -> "SourceCatalog":
        return cls(documents, parse_source_labels_json(source_labels_json))

    def type_info(self, type_key: str) -> dict[str, str]:
        return self.type_defs.get(type_key, self.type_defs["document"])

    def describe_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        type_key = document_source_type_key(doc.get("source_type"))
        type_def = self.type_info(type_key)
        display_name = (doc.get("citation_display_name") or doc.get("title") or "").strip()
        return {
            "type": type_key,
            "shorthand": type_def["shorthand"],
            "css_class": type_def["css_class"],
            "type_display": type_def["display"],
            "display_name": display_name,
            "document_id": doc.get("id"),
        }

    def describe(self, raw_label: str) -> dict[str, Any]:
        label = (raw_label or "").strip()
        lower = label.casefold()

        if lower.startswith("patient context"):
            return self._build_entry("patient_context", label, display_label=self.type_info("patient_context")["display"])

        if "inference" in lower and "not verified" in lower:
            return self._build_entry("inference", label, display_label=self.type_info("inference")["display"])

        if lower.startswith("unknown"):
            return self._build_entry("unknown", label, display_label=self.type_info("unknown")["display"])

        title = parse_document_title(label)
        if title is not None:
            doc = self.doc_index.get(title.casefold())
            type_key = document_source_type_key(doc.get("source_type") if doc else None)
            display_name = (
                (doc.get("citation_display_name") if doc else None)
                or title
            ).strip()
            return self._build_entry(
                type_key,
                label,
                display_label=display_name,
                document_id=doc.get("id") if doc else None,
                document_title=title,
            )

        return self._build_entry("inference", label, display_label=label)

    def _build_entry(
        self,
        type_key: str,
        raw_label: str,
        *,
        display_label: str,
        document_id: str | None = None,
        document_title: str | None = None,
    ) -> dict[str, Any]:
        type_def = self.type_info(type_key)
        return {
            "type": type_key,
            "shorthand": type_def["shorthand"],
            "css_class": type_def["css_class"],
            "type_display": type_def["display"],
            "display_label": display_label,
            "raw_label": raw_label,
            "document_id": document_id,
            "document_title": document_title,
        }

    def enrich_reference(self, raw_label: str, num: int) -> dict[str, Any]:
        info = self.describe(raw_label)
        return {"num": num, "label": info["display_label"], **info}

    def legend(self) -> list[dict[str, str]]:
        return [
            {
                "type": key,
                "display": self.type_defs[key]["display"],
                "shorthand": self.type_defs[key]["shorthand"],
                "css_class": self.type_defs[key]["css_class"],
                "description": self.type_defs[key]["description"],
            }
            for key in SOURCE_TYPE_KEYS
        ]

    def custom_labels(self) -> dict[str, dict[str, str]]:
        custom: dict[str, dict[str, str]] = {}
        for key in SOURCE_TYPE_KEYS:
            current = self.type_defs[key]
            default = DEFAULT_SOURCE_TYPES[key]
            overrides: dict[str, str] = {}
            if current["display"] != default["display"]:
                overrides["display"] = current["display"]
            if current["shorthand"] != default["shorthand"]:
                overrides["shorthand"] = current["shorthand"]
            if overrides:
                custom[key] = overrides
        return custom
