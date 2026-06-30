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

    return {
        "executive_summary": executive_summary,
        "open_items": open_items,
    }


def open_items_to_json(items: list[dict[str, str]]) -> str:
    return json.dumps(items)
