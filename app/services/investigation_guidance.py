DEFAULT_INVESTIGATION_GUIDANCE = (
    "Provide a standard focused investigation. Follow the section structure below."
)


def normalize_investigation_guidance(guidance: str | None) -> str:
    text = (guidance or "").strip()
    return text or DEFAULT_INVESTIGATION_GUIDANCE
