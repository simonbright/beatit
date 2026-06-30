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


def _parse_iso_datetime(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_eastern(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN)


def _eastern_tz_label(dt: datetime) -> str:
    label = _to_eastern(dt).strftime("%Z")
    return "ET" if label in ("EST", "EDT") else label


def _format_eastern_date(dt: datetime) -> str:
    return _to_eastern(dt).strftime("%B %d, %Y").replace(" 0", " ")


def _format_eastern_time(dt: datetime) -> str:
    eastern = _to_eastern(dt)
    hour = eastern.strftime("%I").lstrip("0") or "12"
    return f"{hour}:{eastern.strftime('%M %p')} {_eastern_tz_label(dt)}"


def _format_eastern(dt: datetime) -> str:
    return f"{_format_eastern_date(dt)} · {_format_eastern_time(dt)}"


def _format_timestamp(iso: str | None) -> str:
    dt = _parse_iso_datetime(iso)
    if not dt:
        return "Unknown date and time"
    return _format_eastern(dt)


def _format_timestamp_parts(iso: str | None) -> tuple[str, str]:
    dt = _parse_iso_datetime(iso)
    if not dt:
        return "Unknown date", "Unknown time"
    return _format_eastern_date(dt), _format_eastern_time(dt)


def _format_filename_stamp(iso: str | None) -> str:
    dt = _parse_iso_datetime(iso)
    if not dt:
        return "unknown"
    return _to_eastern(dt).strftime("%Y-%m-%d_%H%M")


def _analysis_type_label(analysis_type: str | None) -> str | None:
    if analysis_type == "baseline":
        return "Baseline assessment"
    if analysis_type == "summarize":
        return "Document summary"
    if analysis_type == "query":
        return "Custom task"
    return None


def _display_title(analysis: dict[str, Any]) -> str | None:
    title = (analysis.get("annotation_title") or "").strip()
    if title:
        return title
    return None


def _write_collaboration_block(pdf: FPDF, analysis: dict[str, Any]) -> None:
    display_title = _display_title(analysis)
    header = (analysis.get("annotation_header") or "").strip()
    notes = (analysis.get("annotation_notes") or "").strip()
    created_by = (analysis.get("created_by") or "").strip()
    query = (analysis.get("query") or "").strip()

    if not any([display_title, header, notes, created_by, query]):
        return

    if display_title:
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(14, 116, 144)
        pdf.multi_cell(0, 8, _safe_text(display_title), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    meta_lines: list[str] = []
    created_at = _format_timestamp(analysis.get("created_at"))
    if created_at != "Unknown date and time":
        meta_lines.append(f"Generated: {created_at}")
    if created_by:
        meta_lines.append(f"By: {created_by}")
    if query and analysis.get("analysis_type") == "query":
        preview = query if len(query) <= 240 else query[:237] + "..."
        meta_lines.append(f"Question: {preview}")
    if meta_lines:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(80, 80, 80)
        for line in meta_lines:
            _pdf_multiline(pdf, _safe_text(line), h=4)
        pdf.ln(2)

    if header:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(0, 0, 0)
        _pdf_multiline(pdf, _break_long_words(_safe_text(header)), h=5)
        pdf.ln(3)

    if notes:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(0, 5, "Collaborator notes", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(0, 0, 0)
        _pdf_multiline(pdf, _break_long_words(_safe_text(notes)), h=5)
        pdf.ln(4)

    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)


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
    def __init__(
        self,
        *,
        report_date: str,
        report_time: str,
        report_type: str | None,
        exported_at: str | None = None,
    ):
        super().__init__()
        self.report_date = report_date
        self.report_time = report_time
        self.report_type = report_type
        self.exported_at = exported_at

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
        self.multi_cell(
            74,
            4,
            _safe_text(self.report_date),
            align="R",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        self.set_x(118)
        self.multi_cell(
            74,
            4,
            _safe_text(self.report_time),
            align="R",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        if self.report_type:
            self.set_x(118)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(80, 80, 80)
            self.cell(74, 4, _safe_text(self.report_type), align="R")

        self.set_y(34)
        self.set_x(self.l_margin)
        self.set_draw_color(14, 165, 233)
        self.set_line_width(0.4)
        self.line(16, self.get_y(), self.w - 16, self.get_y())
        self.ln(6)

    def footer(self) -> None:
        self.set_y(-28)
        if self.exported_at:
            self.set_font("Helvetica", "", 7)
            self.set_text_color(100, 100, 100)
            self.cell(0, 3, _safe_text(f"Exported {self.exported_at}"), align="C", new_x="LMARGIN", new_y="NEXT")
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


def _pdf_multiline(
    pdf: FPDF,
    text: str,
    *,
    h: float = 5,
    w: float = 0,
    align: str = "L",
) -> None:
    pdf.multi_cell(w, h, text, align=align, new_x="LMARGIN", new_y="NEXT")


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
            _pdf_multiline(pdf, title, h=6)
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(0, 0, 0)
            continue

        numbered = re.match(r"^(\d+[\).\:-])\s+(.+)$", stripped)
        bullet = re.match(r"^[-*•]\s+(.+)$", stripped)
        if numbered:
            prefix = numbered.group(1)
            content = _break_long_words(_safe_text(numbered.group(2)))
            _pdf_multiline(pdf, f"  {prefix} {content}", h=5)
        elif bullet:
            content = _break_long_words(_safe_text(bullet.group(1)))
            _pdf_multiline(pdf, f"  - {content}", h=5)
        else:
            _pdf_multiline(pdf, _break_long_words(_safe_text(stripped)), h=5)


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
        _pdf_multiline(pdf, f"  {prefix} {label}", h=4)
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
        _pdf_multiline(pdf, "(No content)", h=5)
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


def _write_appendix_references(
    pdf: FPDF,
    appendix: list[dict[str, Any]],
    *,
    as_of: str | None = None,
) -> None:
    if not appendix:
        return

    pdf.ln(8)
    if pdf.get_y() > pdf.h - pdf.b_margin - 36:
        pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 116, 144)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 10, "Appendix: References", new_x="LMARGIN", new_y="NEXT", align="L")
    if as_of:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, f"As of {_safe_text(as_of)}", new_x="LMARGIN", new_y="NEXT", align="L")
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
        _pdf_multiline(pdf, f"{prefix} {label}", h=5)
        pdf.ln(1)


def build_assessment_pdf(
    analysis: dict[str, Any],
    *,
    patient_context: str | None = None,
    catalog: SourceCatalog | None = None,
) -> bytes:
    report_timestamp = _format_timestamp(analysis.get("created_at"))
    report_date, report_time = _format_timestamp_parts(analysis.get("created_at"))
    exported_at = _format_timestamp(datetime.now(timezone.utc).isoformat())
    report_type = _analysis_type_label(analysis.get("analysis_type"))
    display_title = _display_title(analysis)
    if display_title and analysis.get("analysis_type") == "query":
        report_type = display_title

    ref_bundle = build_reference_bundle(
        executive_summary=analysis.get("executive_summary") or "",
        response=analysis.get("response") or "",
        patient_context=patient_context or "",
        catalog=catalog,
    )

    pdf = AssessmentPDF(
        report_date=report_date,
        report_time=report_time,
        report_type=report_type,
        exported_at=exported_at,
    )
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=28)
    pdf.set_margins(18, 38, 18)
    pdf.add_page()

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(80, 80, 80)
    meta_bits = [f"Generated: {report_timestamp}", f"Model: {analysis.get('model') or 'Unknown'}"]
    if analysis.get("created_by"):
        meta_bits.append(f"By: {analysis.get('created_by')}")
    pdf.cell(0, 5, _safe_text(" · ".join(meta_bits)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    _pdf_multiline(
        pdf,
        _safe_text(
            "Decision-support only — not a substitute for in-person oncology care. "
            "Bracketed numbers [1], [2], ... cite the reference lists below and in the appendix."
        ),
        h=4,
    )
    pdf.ln(4)

    _write_collaboration_block(pdf, analysis)

    response_title = "Latest Assessment"
    if analysis.get("analysis_type") == "query":
        response_title = "Full response"

    _write_section_with_references(
        pdf,
        "Executive Summary",
        ref_bundle["sections"]["executive_summary"],
        as_of=report_timestamp,
    )
    _write_section_with_references(
        pdf,
        response_title,
        ref_bundle["sections"]["response"],
        as_of=report_timestamp,
    )

    patient_section = ref_bundle["sections"]["patient_context"]
    if patient_context and patient_context.strip() and patient_section.get("body", "").strip():
        pdf.add_page()
        _write_section_with_references(
            pdf,
            "Patient Context (Settings)",
            patient_section,
            as_of=report_timestamp,
        )

    _write_appendix_references(pdf, ref_bundle["appendix"], as_of=report_timestamp)

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def assessment_pdf_filename(analysis: dict[str, Any]) -> str:
    stamp = _format_filename_stamp(analysis.get("created_at"))

    title = (analysis.get("annotation_title") or "").strip()
    if title:
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")[:40]
        if slug:
            prefix = "custom-task" if analysis.get("analysis_type") == "query" else "assessment"
            return f"beatit-{prefix}-{slug}-{stamp}.pdf"

    if analysis.get("analysis_type") == "query":
        return f"beatit-custom-task-{stamp}.pdf"
    return f"beatit-assessment-{stamp}.pdf"
