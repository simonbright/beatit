import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from fpdf import FPDF

from app.version import APP_NAME, APP_UPDATED, APP_VERSION


def _safe_text(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\xa0": " ",
        "\u00d7": "x",
        "\u2192": "->",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _format_timestamp(iso: str | None) -> str:
    if not iso:
        return "Unknown date"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y at %H:%M UTC")
    except ValueError:
        return iso


def _analysis_type_label(analysis_type: str | None) -> str:
    if analysis_type == "baseline":
        return "Baseline assessment"
    if analysis_type == "summarize":
        return "Document summary"
    return "Custom query"


class AssessmentPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _break_long_words(text: str, limit: int = 72) -> str:
    parts: list[str] = []
    for word in text.split(" "):
        while len(word) > limit:
            parts.append(word[:limit])
            word = word[limit:]
        if word:
            parts.append(word)
    return " ".join(parts)


def _write_body_lines(pdf: FPDF, body: str) -> None:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            pdf.ln(3)
            continue

        pdf.set_x(pdf.l_margin)

        header_match = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if header_match:
            level = len(header_match.group(1))
            title = _break_long_words(_safe_text(header_match.group(2)))
            pdf.ln(2 if level > 1 else 4)
            pdf.set_font("Helvetica", "B", 13 if level == 1 else 11)
            pdf.set_text_color(20, 60, 90)
            pdf.multi_cell(0, 6, title)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(0, 0, 0)
            continue

        numbered = re.match(r"^(\d+[\).\:-])\s+(.+)$", stripped)
        bullet = re.match(r"^[-*•]\s+(.+)$", stripped)
        if numbered:
            prefix = numbered.group(1)
            content = _break_long_words(_safe_text(numbered.group(2)))
            pdf.multi_cell(0, 5, f"  {prefix} {content}")
        elif bullet:
            content = _break_long_words(_safe_text(bullet.group(1)))
            pdf.multi_cell(0, 5, f"  - {content}")
        else:
            pdf.multi_cell(0, 5, _break_long_words(_safe_text(stripped)))


def _write_section(pdf: FPDF, title: str, body: str) -> None:
    if not body.strip():
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.multi_cell(0, 5, "(No content)")
        pdf.set_text_color(0, 0, 0)
        return

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 116, 144)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(0, 0, 0)
    _write_body_lines(pdf, body)
    pdf.ln(4)


def build_assessment_pdf(
    analysis: dict[str, Any],
    *,
    patient_context: str | None = None,
) -> bytes:
    pdf = AssessmentPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(14, 116, 144)
    pdf.cell(0, 10, "BeatIt", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, "Oncology Case Assessment", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    meta_lines = [
        f"Assessment date: {_format_timestamp(analysis.get('created_at'))}",
        f"Type: {_analysis_type_label(analysis.get('analysis_type'))}",
        f"Model: {analysis.get('model') or 'Unknown'}",
    ]
    for line in meta_lines:
        pdf.cell(0, 5, _safe_text(line), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)

    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0,
        4,
        _safe_text(
            "Decision-support only — not a substitute for in-person oncology care. "
            "Source tags indicate evidence origin; staging requires document evidence."
        ),
    )
    pdf.ln(4)

    _write_section(pdf, "Executive Summary", analysis.get("executive_summary") or "")
    _write_section(pdf, "Latest Assessment", analysis.get("response") or "")

    if patient_context and patient_context.strip():
        pdf.add_page()
        _write_section(pdf, "Patient Context (Settings)", patient_context.strip())

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    exported = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.multi_cell(
        0,
        4,
        _safe_text(
            f"Exported from {APP_NAME} v{APP_VERSION} ({APP_UPDATED}) on {exported}."
        ),
    )

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def assessment_pdf_filename(analysis: dict[str, Any]) -> str:
    created = analysis.get("created_at") or ""
    date_part = "unknown-date"
    if created:
        try:
            date_part = datetime.fromisoformat(
                created.replace("Z", "+00:00")
            ).strftime("%Y-%m-%d")
        except ValueError:
            date_part = created[:10] or date_part
    return f"beatit-assessment-{date_part}.pdf"
