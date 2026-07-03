import json
import re
from typing import Any


def _normalize_header(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def extract_section(response: str, header_keywords: list[str]) -> str:
    lines = response.splitlines()
    capture: list[str] = []
    in_section = False

    for line in lines:
        header_match = re.match(r"^#{1,3}\s*(.+)$", line.strip())
        if header_match:
            header = _normalize_header(header_match.group(1))
            if any(keyword in header for keyword in header_keywords):
                in_section = True
                continue
            if in_section:
                break

        numbered_match = re.match(r"^\d+\.\s+(.+)$", line.strip())
        if numbered_match and not in_section:
            header = _normalize_header(numbered_match.group(1))
            if any(keyword in header for keyword in header_keywords):
                in_section = True
                continue

        if in_section:
            capture.append(line)

    return "\n".join(capture).strip()


_EXECUTIVE_SUMMARY_HEADERS = ("executive summary", "1 executive summary")


def _section_header_name(line: str) -> str | None:
    stripped = line.strip()
    match = re.match(r"^(?:#{1,3}\s*|\d+\.\s+)(.+)$", stripped)
    if not match:
        return None
    return _normalize_header(match.group(1))


def strip_executive_summary_section(response: str) -> str:
    """Remove the executive summary section from a full assessment (PDF exports it separately)."""
    if not response:
        return ""

    lines = response.splitlines()
    result: list[str] = []
    skipping = False

    for line in lines:
        header = _section_header_name(line)
        if header is not None:
            if any(keyword in header for keyword in _EXECUTIVE_SUMMARY_HEADERS):
                skipping = True
                continue
            skipping = False

        if not skipping:
            result.append(line)

    return "\n".join(result).strip()


def _bullet_lines(section_text: str) -> list[str]:
    items: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^(\d+[\).\:-]|[-*•])\s+(.+)$", stripped)
        if match:
            items.append(match.group(2).strip())
        elif stripped.endswith(":") and len(stripped) < 80:
            continue
        elif items and not stripped.startswith("#"):
            items[-1] = f"{items[-1]} {stripped}"
    return items


_SOURCE_TAG_ONLY = re.compile(
    r"^(\[SOURCE:\s*.+\]|SOURCE:\s*Document\s+\"[^\"]+\"|\[SOURCE:\s*Unknown\])\s*$",
    re.IGNORECASE,
)


def _substantive_length(text: str) -> int:
    if not text:
        return 0
    cleaned = re.sub(r"\[SOURCE:\s*[^\]]+\]", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"SOURCE:\s*Document\s+\"[^\"]+\"", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"SOURCE:\s*Unknown[^\n]*", "", cleaned, flags=re.IGNORECASE)
    return len(re.sub(r"\s+", " ", cleaned).strip())


def ensure_executive_summary(parsed: dict[str, Any], response: str) -> str:
    """Use a substantive executive summary, falling back to the full response when needed."""
    summary = (parsed.get("executive_summary") or "").strip()
    full = (response or "").strip()

    if _substantive_length(summary) >= 120:
        return summary

    what_we_know = extract_section(full, ["what we know", "2 what we know"]).strip()
    if what_we_know and _substantive_length(what_we_know) > _substantive_length(summary):
        return what_we_know[:2000]

    if not summary and full:
        summary = full.strip().split("\n\n")[0][:1200]

    if _substantive_length(summary) >= 120:
        return summary

    chunks: list[str] = []
    for block in re.split(r"\n\s*\n", full):
        block = block.strip()
        if not block or block.startswith("#"):
            continue
        if _SOURCE_TAG_ONLY.match(block):
            continue
        if _substantive_length(block) < 40:
            continue
        chunks.append(block)
        if sum(_substantive_length(c) for c in chunks) >= 120:
            break
    if chunks:
        return "\n\n".join(chunks)[:2000]

    return summary


def parse_assessment(response: str) -> dict[str, Any]:
    executive_summary = extract_section(
        response,
        ["executive summary", "1 executive summary"],
    )

    open_sections: list[tuple[str, str]] = [
        ("Gap", extract_section(response, ["critical gaps", "gaps to close"])),
        (
            "Next step",
            extract_section(
                response,
                ["next steps", "open items", "next steps and open items"],
            ),
        ),
        (
            "Question",
            extract_section(response, ["questions for the oncology team", "questions for oncology"]),
        ),
    ]

    open_items: list[dict[str, str]] = []
    priority = 1
    for item_type, section_text in open_sections:
        for item in _bullet_lines(section_text):
            open_items.append(
                {
                    "priority": str(priority),
                    "item": item,
                    "type": item_type,
                    "status": "open",
                }
            )
            priority += 1

    if not executive_summary and response.strip():
        executive_summary = response.strip().split("\n\n")[0][:1200]

    parsed = {
        "executive_summary": executive_summary,
        "open_items": open_items,
    }
    parsed["executive_summary"] = ensure_executive_summary(parsed, response)
    return parsed


def open_items_to_json(items: list[dict[str, str]]) -> str:
    return json.dumps(items)
