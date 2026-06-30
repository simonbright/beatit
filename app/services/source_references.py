import re
from typing import Any

from app.services.source_catalog import SourceCatalog

SOURCE_TAG_PATTERN = re.compile(r"\[SOURCE:\s*([^\]]+)\]", re.IGNORECASE)

BOILERPLATE_SECTION_HEADERS = (
    "source key",
    "sources",
    "references",
    "disclaimer",
)

SECTION_HEADER_PATTERN = re.compile(
    r"^(?:#{1,3}\s*|\d+\.\s+)(.+)$",
    re.MULTILINE,
)


def extract_source_labels(text: str) -> list[str]:
    if not text:
        return []
    return [match.group(1).strip() for match in SOURCE_TAG_PATTERN.finditer(text)]


def unique_preserve_order(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for label in labels:
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(label)
    return ordered


def _normalize_label_key(label: str) -> str:
    return label.strip().casefold()


def strip_boilerplate_sections(text: str) -> str:
    if not text:
        return ""

    lines = text.splitlines()
    kept: list[str] = []
    skipping = False

    for line in lines:
        stripped = line.strip()
        header_match = re.match(r"^(?:#{1,3}\s*|\d+\.\s+)(.+)$", stripped)
        if header_match:
            header = re.sub(r"[^a-z0-9 ]+", "", header_match.group(1).lower()).strip()
            if any(keyword in header for keyword in BOILERPLATE_SECTION_HEADERS):
                skipping = True
                continue
            skipping = False

        if not skipping:
            kept.append(line)

    return "\n".join(kept).strip()


def replace_source_tags_with_numbers(text: str, registry: dict[str, int]) -> str:
    if not text:
        return ""

    def repl(match: re.Match[str]) -> str:
        label = match.group(1).strip()
        number = registry.get(_normalize_label_key(label))
        if number is None:
            return match.group(0)
        return f"[{number}]"

    return SOURCE_TAG_PATTERN.sub(repl, text)


def section_reference_entries(
    text: str,
    registry: dict[str, int],
    catalog: SourceCatalog | None = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[int] = set()
    for label in extract_source_labels(text):
        number = registry.get(_normalize_label_key(label))
        if number is None or number in seen:
            continue
        seen.add(number)
        if catalog:
            entries.append(catalog.enrich_reference(label, number))
        else:
            entries.append({"num": number, "label": label, "raw_label": label})
    entries.sort(key=lambda item: item["num"])
    return entries


def format_reference_label(label: str, catalog: SourceCatalog | None = None) -> str:
    if catalog:
        return catalog.describe(label)["display_label"]
    cleaned = label.strip()
    lower = cleaned.casefold()
    if lower.startswith("document "):
        title = cleaned[9:].strip().strip('"')
        return title or cleaned
    if lower.startswith("patient context"):
        return "Patient context (settings — not verified clinical record)"
    if "inference" in lower and "not verified" in lower:
        return "AI inference — not verified"
    if lower.startswith("unknown"):
        return "Not in stored records — verify externally"
    return cleaned


def build_reference_registry(
    *texts: str,
    catalog: SourceCatalog | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    labels: list[str] = []
    for text in texts:
        labels.extend(extract_source_labels(strip_boilerplate_sections(text)))

    appendix_labels = unique_preserve_order(labels)
    registry = {_normalize_label_key(label): index + 1 for index, label in enumerate(appendix_labels)}
    appendix: list[dict[str, Any]] = []
    for label in appendix_labels:
        num = registry[_normalize_label_key(label)]
        if catalog:
            appendix.append(catalog.enrich_reference(label, num))
        else:
            appendix.append(
                {
                    "num": num,
                    "label": format_reference_label(label),
                    "raw_label": label,
                }
            )
    return registry, appendix


def prepare_section(
    text: str,
    registry: dict[str, int],
    catalog: SourceCatalog | None = None,
) -> dict[str, Any]:
    cleaned = strip_boilerplate_sections(text or "")
    body = replace_source_tags_with_numbers(cleaned, registry)
    references = section_reference_entries(cleaned, registry, catalog)
    return {"body": body, "references": references}


def build_reference_bundle(
    *,
    executive_summary: str | None = None,
    response: str | None = None,
    patient_context: str | None = None,
    catalog: SourceCatalog | None = None,
) -> dict[str, Any]:
    summary_raw = executive_summary or ""
    response_raw = response or ""
    context_raw = patient_context or ""

    registry, appendix = build_reference_registry(
        summary_raw,
        response_raw,
        context_raw,
        catalog=catalog,
    )
    sections = {
        "executive_summary": prepare_section(summary_raw, registry, catalog),
        "response": prepare_section(response_raw, registry, catalog),
        "patient_context": prepare_section(context_raw, registry, catalog),
    }
    registry_by_num = {entry["num"]: entry for entry in appendix}
    return {
        "appendix": appendix,
        "sections": sections,
        "registry": registry,
        "registry_by_num": registry_by_num,
    }
