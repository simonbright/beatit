import re
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from zoneinfo import ZoneInfo

from fpdf import FPDF

from app.services.source_catalog import SourceCatalog
from app.services.source_references import build_reference_bundle, format_reference_label
from app.version import APP_NAME, APP_VERSION

EASTERN = ZoneInfo("America/New_York")


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


def _format_eastern(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    eastern = dt.astimezone(EASTERN)
    hour = eastern.strftime("%I").lstrip("0") or "12"
    return (
        f"{eastern.strftime('%A, %B')} {eastern.day}, {eastern.strftime('%Y')} "
        f"at {hour}:{eastern.strftime('%M %p')} EST"
    )


def _format_timestamp(iso: str | None) -> str:
    if not iso:
        return "Unknown date"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return _format_eastern(dt)
    except ValueError:
        return iso


def _analysis_type_label(analysis_type: str | None) -> str | None:
    if analysis_type == "baseline":
        return "Baseline assessment"
    if analysis_type == "summarize":
        return "Document summary"
    return None


def _draw_duck_logo(pdf: FPDF, x: float, y: float, scale: float = 1.0) -> None:
    s = scale
    pdf.set_fill_color(125, 211, 252)
    pdf.ellipse(x, y, 18 * s, 18 * s, style="F")
    pdf.set_fill_color(250, 204, 21)
    pdf.ellipse(x + 2 * s, y + 8 * s, 12 * s, 8 * s, style="F")
    pdf.ellipse(x + 4 * s, y + 1 * s, 9 * s, 9 * s, style="F")
    pdf.set_fill_color(30, 41, 59)
    pdf.ellipse(x + 10 * s, y + 3 * s, 1.6 * s, 1.6 * s, style="F")
    pdf.set_fill_color(251, 146, 60)
    pdf.ellipse(x + 13 * s, y + 5 * s, 3 * s, 2 * s, style="F")


class AssessmentPDF(FPDF):
    def __init__(self, *, report_timestamp: str, report_type: str | None):
        super().__init__()
        self.report_timestamp = report_timestamp
        self.report_type = report_type

    def header(self) -> None:
        _draw_duck_logo(self, 16, 9, 1.15)
        self.set_xy(40, 11)
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(14, 116, 144)
        self.cell(0, 7, "BeatIt", new_x="LMARGIN", new_y="NEXT")
        self.set_x(40)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(80, 80, 80)
        self.cell(0, 5, "Oncology Case Analysis", new_x="LMARGIN", new_y="NEXT")

        self.set_xy(118, 11)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        self.multi_cell(74, 4, _safe_text(self.report_timestamp), align="R")
        if self.report_type:
            self.set_x(118)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(80, 80, 80)
            self.cell(74, 4, _safe_text(self.report_type), align="R")

        self.set_y(32)
        self.set_draw_color(14, 165, 233)
        self.set_line_width(0.4)
        self.line(16, self.get_y(), self.w - 16, self.get_y())
        self.ln(6)

    def footer(self) -> None:
        self.set_y(-24)
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(60, 60, 60)
        self.cell(0, 4, "Medical Confidential - Susan Brajtman", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 8)
        self.cell(0, 4, "sbrajtman@gmail.com  1-613-614-7536", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 4, f"Page {self.page_no()}/{{nb}} · {APP_NAME} v{APP_VERSION}", align="C")


def _break_long_words(text: str, limit: int = 72) -> str:
    parts: list[str] = []
    for word in text.split(" "):
        while len(word) > limit:
            parts.append(word[:limit])
            word = word[limit:]
        if word:
            parts.append(word)
    return " ".join(parts)


def _clean_markdown_emphasis(text: str) -> str:
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", text)


def _write_body_lines(pdf: FPDF, body: str) -> None:
    body = _clean_markdown_emphasis(body)
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


def _write_references_block(
    pdf: FPDF,
    references: list[dict[str, Any]],
    *,
    heading: str = "References",
) -> None:
    if not references:
        return

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(70, 70, 70)
    pdf.cell(0, 5, heading, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(40, 40, 40)
    for entry in references:
        shorthand = _safe_text(entry.get("shorthand") or "")
        label = _safe_text(entry.get("display_label") or entry.get("label") or "")
        prefix = f"[{entry['num']}]"
        if shorthand:
            prefix = f"{prefix} [{shorthand}]"
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 4, f"  {prefix} {label}")
    pdf.ln(2)


def _write_section_with_references(
    pdf: FPDF,
    title: str,
    section: dict[str, Any],
    *,
    as_of: str | None = None,
) -> None:
    body = section.get("body") or ""
    references = section.get("references") or []

    if not body.strip():
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.multi_cell(0, 5, "(No content)")
        pdf.set_text_color(0, 0, 0)
        return

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 116, 144)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="L")
    if as_of:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, f"As of {_safe_text(as_of)}", new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(0, 0, 0)
    _write_body_lines(pdf, body)
    _write_references_block(pdf, references)
    pdf.ln(4)


def _write_appendix_references(pdf: FPDF, appendix: list[dict[str, Any]]) -> None:
    if not appendix:
        return

    pdf.ln(8)
    if pdf.get_y() > pdf.h - pdf.b_margin - 36:
        pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 116, 144)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 10, "Appendix: References", new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0, 0, 0)
    for entry in appendix:
        shorthand = _safe_text(entry.get("shorthand") or "")
        label = _safe_text(
            entry.get("display_label") or entry.get("label") or format_reference_label(entry.get("raw_label", ""))
        )
        prefix = f"[{entry['num']}]"
        if shorthand:
            prefix = f"{prefix} [{shorthand}]"
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, f"{prefix} {label}", align="L")
        pdf.ln(1)


def build_assessment_pdf(
    analysis: dict[str, Any],
    *,
    patient_context: str | None = None,
    catalog: SourceCatalog | None = None,
) -> bytes:
    report_timestamp = _format_timestamp(analysis.get("created_at"))
    report_type = _analysis_type_label(analysis.get("analysis_type"))

    ref_bundle = build_reference_bundle(
        executive_summary=analysis.get("executive_summary") or "",
        response=analysis.get("response") or "",
        patient_context=patient_context or "",
        catalog=catalog,
    )

    pdf = AssessmentPDF(report_timestamp=report_timestamp, report_type=report_type)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=28)
    pdf.set_margins(18, 38, 18)
    pdf.add_page()

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 5, _safe_text(f"Model: {analysis.get('model') or 'Unknown'}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.multi_cell(
        0,
        4,
        _safe_text(
            "Decision-support only — not a substitute for in-person oncology care. "
            "Bracketed numbers [1], [2], ... cite the reference lists below and in the appendix."
        ),
    )
    pdf.ln(4)

    _write_section_with_references(
        pdf,
        "Executive Summary",
        ref_bundle["sections"]["executive_summary"],
        as_of=report_timestamp,
    )
    _write_section_with_references(
        pdf,
        "Latest Assessment",
        ref_bundle["sections"]["response"],
    )

    patient_section = ref_bundle["sections"]["patient_context"]
    if patient_context and patient_context.strip() and patient_section.get("body", "").strip():
        pdf.add_page()
        _write_section_with_references(
            pdf,
            "Patient Context (Settings)",
            patient_section,
        )

    _write_appendix_references(pdf, ref_bundle["appendix"])

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
