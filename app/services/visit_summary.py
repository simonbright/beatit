"""Turn visit notes / transcripts into a few factual timeline bullets."""

from __future__ import annotations

import re
from typing import Any

MAX_BULLETS = 6
MIN_BULLETS = 3
MAX_BULLET_CHARS = 160
MAX_SOURCE_CHARS = 16_000
MAX_USER_SUMMARY_CHARS = 900
MAX_USER_SUMMARY_LINES = 8
PLANISH = re.compile(
    r"\b(plan|follow[- ]?up|biopsy|fast|npo|midnight|cancer board|hematology|"
    r"antibiotic|book|schedule|will (?:start|stop|transition|order|keep)|"
    r"next (?:step|ct|scan|visit)|continue)\b",
    re.I,
)


def is_short_visit_summary(text: str) -> bool:
    """True when the family typed a short recap, not a dumped transcript."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if len(raw) > MAX_USER_SUMMARY_CHARS:
        return False
    lines = [line for line in raw.splitlines() if line.strip()]
    return len(lines) <= MAX_USER_SUMMARY_LINES

VISIT_SUMMARY_SYSTEM = """You write a short family-timeline summary of a recorded clinical visit.
Use only facts that appear in the notes: what was said or done, who said it, numbers, plans, and next steps.
Do not diagnose, advise, infer, or soften the wording.
Reply with 3 to 6 bullets and nothing else. Each bullet starts with "- " and is one sentence under 160 characters."""


def format_visit_bullets(lines: list[str]) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        line = re.sub(r"^[\s>*\-•–—\d\.\)\(]+", "", str(raw or "")).strip()
        line = " ".join(line.split())
        if not line:
            continue
        if line[-1] not in ".!?":
            line = f"{line}."
        if len(line) > MAX_BULLET_CHARS:
            line = line[: MAX_BULLET_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;") + "."
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(f"- {line}")
        if len(cleaned) >= MAX_BULLETS:
            break
    return "\n".join(cleaned)


def parse_visit_bullets(text: str) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"^(\d+[\.\)]\s+|[-*•–—]\s+)", "", line).strip()
        if line:
            lines.append(line)
    return lines


def fallback_visit_bullets(chunks: list[tuple[str, str]]) -> str:
    """Pull a few concrete lines from the notes when the LLM is unavailable."""
    candidates: list[str] = []
    for _name, body in chunks:
        for raw in re.split(r"(?<=[.!?])\s+|\n+", body or ""):
            line = " ".join(raw.split()).strip(" -•")
            if len(line) < 24:
                continue
            if re.match(r"^(thanks|thank you|ok|okay|yes|no|yeah|so|and)\b", line, re.I):
                continue
            candidates.append(line)
    if not candidates:
        return ""
    ranked = sorted(
        candidates,
        key=lambda line: (1 if PLANISH.search(line) else 0, -len(line)),
        reverse=True,
    )
    return format_visit_bullets(ranked[:MAX_BULLETS])


def _source_prompt(chunks: list[tuple[str, str]], *, who: str, role: str) -> str:
    parts: list[str] = []
    if who or role:
        parts.append(f"Visit with: {who}{f' ({role})' if role else ''}".strip())
    used = 0
    for name, body in chunks:
        text = (body or "").strip()
        if not text:
            continue
        remaining = MAX_SOURCE_CHARS - used
        if remaining <= 200:
            break
        if len(text) > remaining:
            text = text[:remaining].rsplit(" ", 1)[0] + "\n…[truncated]"
        label = name or "Notes"
        parts.append(f"=== {label} ===\n{text}")
        used += len(text)
    return "\n\n".join(parts)


async def summarize_visit_notes(
    chunks: list[tuple[str, str]],
    *,
    llm: Any | None,
    who: str = "",
    role: str = "",
) -> tuple[str, str]:
    """Return (bullet summary, source) where source is 'ai' or 'fallback'."""
    usable = [(name, body) for name, body in chunks if (body or "").strip()]
    fallback = fallback_visit_bullets(usable)
    if not usable:
        return "", "fallback"
    if llm is None:
        return fallback, "fallback"
    prompt = _source_prompt(usable, who=who, role=role)
    if not prompt.strip():
        return fallback, "fallback"
    try:
        raw = await llm.generate(
            prompt=prompt,
            system=VISIT_SUMMARY_SYSTEM,
            temperature=0.15,
        )
    except Exception:
        return fallback, "fallback"
    bullets = format_visit_bullets(parse_visit_bullets(raw))
    if not bullets:
        return fallback, "fallback"
    if bullets.count("\n") + 1 < min(MIN_BULLETS, 2) and fallback:
        return fallback, "fallback"
    return bullets, "ai"
