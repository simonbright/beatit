import re

PALLIATIVE_EXCLUSION = """
ABSOLUTE EXCLUSION — DO NOT MENTION PALLIATIVE CARE:
- Never use the words "palliative", "palliative care", "comfort care", "hospice", or "end-of-life care"
- Do not recommend, discuss, or reference palliative care in any section (including treatment options, radiation, or supportive care)
- For symptom relief (e.g. biliary obstruction), discuss drainage, stenting, or supportive interventions WITHOUT using the word palliative
- If a stored document mentions palliative care, do NOT repeat it in your synthesis — omit it entirely
- This rule overrides all other instructions
"""

_PALLIATIVE_LINE = re.compile(r"\bpalliative\b|\bcomfort\s+care\b|\bhospice\b", re.IGNORECASE)


def filter_palliative_content(text: str | None) -> str:
    """Remove lines that mention palliative/hospice/comfort care from stored or generated text."""
    if not text:
        return ""

    kept: list[str] = []
    for line in text.splitlines():
        if _PALLIATIVE_LINE.search(line):
            continue
        kept.append(line)

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return result
