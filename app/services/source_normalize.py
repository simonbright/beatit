import re
from difflib import SequenceMatcher


SOURCE_TAG_PATTERN = re.compile(r"\[SOURCE:\s*[^\]]+\]", re.IGNORECASE)


def has_source_tags(text: str) -> bool:
    return bool(text and SOURCE_TAG_PATTERN.search(text))


def _title_match_score(ref: str, title: str) -> float:
    ref = ref.strip().lower()
    title_l = title.strip().lower()
    if ref == title_l:
        return 1.0
    if ref in title_l or title_l in ref:
        return 0.92
    return SequenceMatcher(None, ref, title_l).ratio()


def _best_title_match(ref: str, titles: list[str]) -> str | None:
    best_title: str | None = None
    best_score = 0.0
    for title in titles:
        score = _title_match_score(ref, title)
        if score > best_score:
            best_score = score
            best_title = title
    if best_score >= 0.72:
        return best_title
    return None


def _paren_to_source_tag(match: re.Match, titles: list[str]) -> str:
    inner = match.group(1).strip()
    if inner.lower().startswith("source:"):
        return match.group(0)
    matched = _best_title_match(inner, titles)
    if matched:
        return f'[SOURCE: Document "{matched}"]'
    return match.group(0)


def normalize_source_attribution(text: str, document_titles: list[str]) -> str:
    if not text or not document_titles:
        return text

    titles = [t for t in document_titles if t and t.strip()]
    if not titles:
        return text

    result = text
    for title in sorted(titles, key=len, reverse=True):
        escaped = re.escape(title)
        result = re.sub(
            rf"\(({escaped})\)",
            f'[SOURCE: Document "{title}"]',
            result,
            flags=re.IGNORECASE,
        )

    result = re.sub(
        r"\(([^)]+)\)",
        lambda m: _paren_to_source_tag(m, titles),
        result,
    )

    return result


def annotate_unsourced_staging(text: str) -> str:
    """Flag staging claims that lack any SOURCE tag on the same line."""
    lines: list[str] = []
    staging_keywords = (
        "stage iv",
        "stage 4",
        "stage iii",
        "stage 3",
        "stage ii",
        "stage 2",
        "stage i",
        "stage 1",
        "tnm",
        "resectable",
        "unresectable",
        "metastatic disease",
        "locally advanced",
    )
    for line in text.splitlines():
        lower = line.lower()
        if any(k in lower for k in staging_keywords) and not SOURCE_TAG_PATTERN.search(line):
            if "[SOURCE:" not in line:
                line = (
                    f"{line.rstrip()} "
                    f"[SOURCE: AI inference — not verified — no document tag on this line]"
                )
        lines.append(line)
    return "\n".join(lines)


def enrich_with_sources(
    text: str,
    document_titles: list[str],
    *,
    annotate_staging: bool = True,
) -> tuple[str, str]:
    """
    Returns (enriched_text, attribution_level).
    attribution_level: full | normalized | missing
    """
    if not text:
        return text, "missing"

    if has_source_tags(text):
        return text, "full"

    normalized = normalize_source_attribution(text, document_titles)
    if has_source_tags(normalized):
        level = "normalized"
    else:
        level = "missing"

    if annotate_staging and level != "full":
        normalized = annotate_unsourced_staging(normalized)

    if has_source_tags(normalized) and level == "missing":
        level = "normalized"

    return normalized, level
