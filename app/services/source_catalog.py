"""Source type catalog — classification, shorthands, colors, and display names."""

import json
import re
from copy import deepcopy
from typing import Any

DOCUMENT_LABEL_RE = re.compile(r'^document\s+"([^"]+)"\s*$', re.IGNORECASE)
CHAT_OBSERVATION_LABEL_RE = re.compile(
    r'^chat\s+observation\s+"([^"]+)"\s*$', re.IGNORECASE
)
URL_IN_TEXT_RE = re.compile(r"""https?://[^\s\]\)"'<>]+""", re.IGNORECASE)
NCT_ID_RE = re.compile(r"\b(NCT\d{8})\b", re.IGNORECASE)
WEB_PREFIX_RE = re.compile(r"^web\s*[—:\-–]\s*", re.IGNORECASE)


def extract_source_uri_from_label(label: str) -> str | None:
    text = (label or "").strip()
    if not text:
        return None
    match = URL_IN_TEXT_RE.search(text)
    if match:
        return match.group(0).rstrip(".,;)")
    nct = NCT_ID_RE.search(text)
    if nct:
        return f"https://clinicaltrials.gov/study/{nct.group(1).upper()}"
    return None


def publisher_label_from_uri(uri: str) -> str:
    from urllib.parse import urlparse

    try:
        host = urlparse(uri).hostname or ""
    except Exception:
        return uri
    host = host.removeprefix("www.")
    if not host:
        return uri
    if host.endswith(".gov"):
        base = host[: -len(".gov")]
        return f"{base} (.gov)" if base else host
    return host

SOURCE_TYPE_KEYS = (
    "document",
    "diagnostic",
    "web",
    "chat_observation",
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
    "chat_observation": {
        "display": "Chat observation",
        "shorthand": "Chat",
        "css_class": "source-chat",
        "description": "User-curated excerpt from AI Chat",
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
        "display": "Not in your library",
        "shorthand": "?",
        "css_class": "source-unknown",
        "description": "Claim not backed by a stored record — do not treat as fact",
    },
}

DOCUMENT_SOURCE_TYPE_MAP: dict[str, str] = {
    "imaging": "diagnostic",
    "pdf": "document",
    "text": "document",
    "chat_observation": "chat_observation",
    "url": "web",
    "youtube": "web",
    "facebook": "web",
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

        chat_title = None
        chat_match = CHAT_OBSERVATION_LABEL_RE.match(label.strip())
        if chat_match:
            chat_title = chat_match.group(1).strip()
        elif lower.startswith("chat observation"):
            remainder = label.strip()[len("chat observation"):].strip().strip('"')
            if remainder:
                chat_title = remainder
        if chat_title is not None:
            doc = self.doc_index.get(chat_title.casefold())
            if doc is None:
                for stored in self.doc_index.values():
                    names = {
                        str(stored.get("title") or "").casefold(),
                        str(stored.get("citation_display_name") or "").casefold(),
                    }
                    if chat_title.casefold() in names:
                        doc = stored
                        break
            type_key = (
                "chat_observation"
                if doc and doc.get("source_type") == "chat_observation"
                else "chat_observation"
            )
            display_name = (
                (doc.get("citation_display_name") if doc else None) or chat_title
            ).strip()
            return self._build_entry(
                type_key,
                label,
                display_label=display_name,
                document_id=doc.get("id") if doc else None,
                document_title=chat_title,
                source_uri=doc.get("source_uri") if doc else None,
            )

        title = parse_document_title(label)
        if title is not None:
            doc = self.doc_index.get(title.casefold())
            if doc is None:
                for stored in self.doc_index.values():
                    names = {
                        str(stored.get("title") or "").casefold(),
                        str(stored.get("citation_display_name") or "").casefold(),
                    }
                    if title.casefold() in names:
                        doc = stored
                        break
            type_key = document_source_type_key(doc.get("source_type") if doc else None)
            display_name = (
                (doc.get("citation_display_name") if doc else None)
                or title
            ).strip()
            source_uri = doc.get("source_uri") if doc else None
            return self._build_entry(
                type_key,
                label,
                display_label=display_name,
                document_id=doc.get("id") if doc else None,
                document_title=title,
                source_uri=source_uri,
            )

        source_uri = extract_source_uri_from_label(label)
        if source_uri:
            cleaned = WEB_PREFIX_RE.sub("", label).strip()
            display = cleaned if cleaned and cleaned != source_uri else publisher_label_from_uri(source_uri)
            if display.startswith("http"):
                display = publisher_label_from_uri(source_uri)
            return self._build_entry(
                "web",
                label,
                display_label=display,
                source_uri=source_uri,
            )

        if WEB_PREFIX_RE.match(label):
            remainder = WEB_PREFIX_RE.sub("", label).strip()
            if remainder:
                return self._build_entry("web", label, display_label=remainder)

        return self._build_entry("inference", label, display_label=label)

    def _build_entry(
        self,
        type_key: str,
        raw_label: str,
        *,
        display_label: str,
        document_id: str | None = None,
        document_title: str | None = None,
        source_uri: str | None = None,
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
            "source_uri": source_uri,
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
