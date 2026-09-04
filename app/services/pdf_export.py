import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fpdf import FPDF

from app.services.assessment_parse import strip_executive_summary_section
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
    return _to_eastern(dt).strftime("%Y-%m-%d_%H%M%S")


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
        patient_label: str | None = None,
        patient_subline: str | None = None,
    ):
        super().__init__()
        self.report_date = report_date
        self.report_time = report_time
        self.report_type = report_type
        self.exported_at = exported_at
        self.patient_label = (patient_label or "").strip() or None
        self.patient_subline = (patient_subline or "").strip() or None

    def header(self) -> None:
        _draw_duck_logo(self, 16, 9, 1.15)
        self.set_xy(40, 11)
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(14, 116, 144)
        self.cell(0, 7, "BeatIt", new_x="LMARGIN", new_y="NEXT")
        self.set_x(40)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(80, 80, 80)
        subtitle = self.patient_label or "Patient Care Workspace"
        self.cell(0, 5, _safe_text(subtitle), new_x="LMARGIN", new_y="NEXT")

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
        confidential = "Medical Confidential"
        if self.patient_label:
            confidential = f"Medical Confidential - {self.patient_label}"
        self.cell(0, 4, _safe_text(confidential), align="C", new_x="LMARGIN", new_y="NEXT")
        if self.patient_subline:
            self.set_font("Helvetica", "", 8)
            self.cell(0, 4, _safe_text(self.patient_subline), align="C", new_x="LMARGIN", new_y="NEXT")
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
    include_section_references: bool = False,
    force_new_page: bool = False,
) -> None:
    body = section.get("body") or ""
    references = section.get("references") or []

    if force_new_page and body.strip():
        pdf.add_page()

    if not body.strip():
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(14, 116, 144)
        pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="L")
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
    if include_section_references:
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

    pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 116, 144)
    pdf.set_x(pdf.l_margin)
    pdf.cell(0, 10, "References", new_x="LMARGIN", new_y="NEXT", align="L")
    if as_of:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, f"As of {_safe_text(as_of)}", new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    _pdf_multiline(
        pdf,
        _safe_text(
            "Inline citations [1], [2], ... map to this list. "
            "Scope documents used in the assessment are included even when not cited inline."
        ),
        h=4,
    )
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
        cited = entry.get("cited")
        note = ""
        if cited is False:
            note = " (in assessment scope - not cited inline)"
        pdf.set_x(pdf.l_margin)
        _pdf_multiline(pdf, _safe_text(f"{prefix} {label}{note}"), h=5)
        pdf.ln(1)


def _normalize_ref_key(value: str | None) -> str:
    return (value or "").strip().casefold()


def _expand_appendix_with_scope_documents(
    appendix: list[dict[str, Any]],
    analysis: dict[str, Any],
    catalog: SourceCatalog | None,
) -> list[dict[str, Any]]:
    """Ensure every assessment-scope document appears in the PDF reference list."""
    expanded = [{**entry, "cited": True} for entry in (appendix or [])]
    seen_ids: set[str] = {
        str(entry.get("document_id"))
        for entry in expanded
        if entry.get("document_id")
    }
    seen_titles: set[str] = set()
    for entry in expanded:
        for key in (
            entry.get("document_title"),
            entry.get("display_label"),
            entry.get("label"),
        ):
            norm = _normalize_ref_key(key)
            if norm:
                seen_titles.add(norm)

    titles = analysis.get("document_titles") or []
    doc_ids = analysis.get("document_ids") or []
    # Prefer id+title pairs when lengths match; otherwise fall back to titles alone.
    pairs: list[tuple[str | None, str]] = []
    if doc_ids and titles and len(doc_ids) == len(titles):
        pairs = list(zip(doc_ids, titles))
    elif titles:
        pairs = [(None, title) for title in titles]
    elif doc_ids:
        pairs = [(doc_id, str(doc_id)) for doc_id in doc_ids]

    next_num = max((int(entry.get("num") or 0) for entry in expanded), default=0) + 1
    for doc_id, title in pairs:
        title = (title or "").strip()
        if not title and not doc_id:
            continue
        title_key = _normalize_ref_key(title)
        if (doc_id and str(doc_id) in seen_ids) or (title_key and title_key in seen_titles):
            continue

        raw_label = f'Document "{title}"' if title else f"Document {doc_id}"
        if catalog:
            entry = catalog.enrich_reference(raw_label, next_num)
        else:
            entry = {
                "num": next_num,
                "label": title or str(doc_id),
                "display_label": title or str(doc_id),
                "raw_label": raw_label,
                "document_id": doc_id,
                "shorthand": "Doc",
                "type": "document",
                "type_display": "Clinical record",
                "css_class": "source-document",
            }
        if doc_id and not entry.get("document_id"):
            entry["document_id"] = doc_id
        entry["cited"] = False
        expanded.append(entry)
        next_num += 1
        if doc_id:
            seen_ids.add(str(doc_id))
        if title_key:
            seen_titles.add(title_key)

    return expanded


def _format_diag_date_label(iso: str | None) -> str:
    raw = str(iso or "")[:10]
    dt = _parse_iso_datetime(raw)
    if not dt:
        return raw or "-"
    # Use day without leading zero via manual format (portable)
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _format_diag_value_label(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value) if value is not None else ""
    if n != n:  # NaN
        return ""
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    abs_n = abs(n)
    if abs_n >= 100:
        return f"{n:.0f}"
    if abs_n >= 10:
        return f"{n:.1f}"
    text = f"{n:.2f}".rstrip("0").rstrip(".")
    return text or "0"


_STATUS_RGB = {
    "green": (21, 128, 61),
    "yellow": (202, 138, 4),
    "red": (185, 28, 28),
}


def _status_rgb(status: str | None, fallback: tuple[int, int, int] = (14, 116, 144)) -> tuple[int, int, int]:
    if not status:
        return fallback
    return _STATUS_RGB.get(str(status).lower(), fallback)


_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _chart_font_paths() -> tuple[Path | None, Path | None]:
    regular = _FONT_DIR / "DejaVuSans.ttf"
    bold = _FONT_DIR / "DejaVuSans-Bold.ttf"
    candidates_regular = [
        regular,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    candidates_bold = [
        bold,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    reg = next((p for p in candidates_regular if p.is_file()), None)
    bld = next((p for p in candidates_bold if p.is_file()), reg)
    return reg, bld


def _load_chart_fonts() -> tuple[Any, Any, Any, Any]:
    """Return (title, value, label, small) Pillow fonts sized for print clarity."""
    from PIL import ImageFont

    regular, bold = _chart_font_paths()
    try:
        if bold and regular:
            return (
                ImageFont.truetype(str(bold), 52),
                ImageFont.truetype(str(bold), 110),
                ImageFont.truetype(str(bold), 44),
                ImageFont.truetype(str(regular), 36),
            )
        if regular:
            return (
                ImageFont.truetype(str(regular), 52),
                ImageFont.truetype(str(regular), 110),
                ImageFont.truetype(str(regular), 44),
                ImageFont.truetype(str(regular), 36),
            )
    except OSError:
        pass
    fallback = ImageFont.load_default()
    return fallback, fallback, fallback, fallback


def _sparkline_png_bytes(
    readings: list[dict[str, Any]],
    *,
    width: int = 1800,
    height: int = 640,
    stroke: tuple[int, int, int] = (14, 116, 144),
    reference: dict[str, Any] | None = None,
    series_status: str | None = None,
    title: str | None = None,
    unit: str | None = None,
) -> bytes | None:
    """Render a print-quality diagnostic chart PNG for embedding in the PDF."""
    points: list[tuple[str, float, str | None]] = []
    for row in readings or []:
        date = str(row.get("recorded_at") or "")[:10]
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if not date:
            continue
        status = row.get("status")
        if isinstance(status, str):
            status = status.lower()
        else:
            status = None
        points.append((date, value, status))
    if not points:
        return None

    from PIL import Image, ImageDraw

    line_stroke = _status_rgb(series_status or points[-1][2], stroke)
    font_title, font_value, font_label, font_small = _load_chart_fonts()

    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    pad_l, pad_r, pad_t, pad_b = 80, 100, 48, 100
    if title:
        draw.text((pad_l, 16), _safe_text(title), fill=(15, 23, 42), font=font_title, anchor="lt")
        pad_t = 84

    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    ref_low = None
    ref_high = None
    if reference:
        try:
            if reference.get("low") is not None:
                ref_low = float(reference["low"])
        except (TypeError, ValueError):
            ref_low = None
        try:
            if reference.get("high") is not None:
                ref_high = float(reference["high"])
        except (TypeError, ValueError):
            ref_high = None

    if len(points) == 1:
        date, value, status = points[0]
        color = _status_rgb(status, line_stroke)
        label = _format_diag_value_label(value)
        if unit:
            label = f"{label} {unit}"
        date_label = _safe_text(_format_diag_date_label(date))
        status_label = (status or "unknown").capitalize()
        cx, cy = width / 2, pad_t + chart_h / 2 - 24

        badge_w, badge_h = 280, 64
        bx0, by0 = cx - badge_w / 2, cy + 90
        draw.rounded_rectangle((bx0, by0, bx0 + badge_w, by0 + badge_h), radius=16, fill=color)
        draw.text((cx, cy - 10), label, fill=color, font=font_value, anchor="mm")
        draw.text((cx, cy + 70), date_label, fill=(51, 65, 85), font=font_label, anchor="mm")
        draw.text((cx, by0 + badge_h / 2), status_label, fill=(255, 255, 255), font=font_label, anchor="mm")
        if reference and reference.get("label"):
            draw.text(
                (cx, by0 + badge_h + 36),
                _safe_text(f"Reference: {reference['label']}"),
                fill=(71, 85, 105),
                font=font_small,
                anchor="mm",
            )
    else:
        values = [p[1] for p in points]
        vmin, vmax = min(values), max(values)
        if ref_low is not None:
            vmin = min(vmin, ref_low)
        if ref_high is not None:
            vmax = max(vmax, ref_high)
        pad = (vmax - vmin) * 0.14 or abs(vmax) * 0.1 or 0.2
        vmin -= pad
        vmax += pad
        span = vmax - vmin or 1.0

        def y_for(val: float) -> float:
            return pad_t + chart_h - ((val - vmin) / span) * chart_h

        draw.rectangle((pad_l, pad_t, pad_l + chart_w, pad_t + chart_h), outline=(148, 163, 184), width=3)

        if ref_low is not None or ref_high is not None:
            top = y_for(ref_high if ref_high is not None else vmax)
            bottom = y_for(ref_low if ref_low is not None else vmin)
            y1, y2 = min(top, bottom), max(top, bottom)
            band = tuple(min(255, int(c * 0.18 + 230)) for c in (21, 128, 61))
            draw.rectangle((pad_l + 2, y1, pad_l + chart_w - 2, y2), fill=band)
            if ref_high is not None:
                y = y_for(ref_high)
                draw.line((pad_l, y, pad_l + chart_w, y), fill=(21, 128, 61), width=4)
                draw.text(
                    (pad_l + chart_w + 10, y),
                    _safe_text(_format_diag_value_label(ref_high)),
                    fill=(21, 128, 61),
                    font=font_small,
                    anchor="lm",
                )
            if ref_low is not None:
                y = y_for(ref_low)
                draw.line((pad_l, y, pad_l + chart_w, y), fill=(21, 128, 61), width=4)
                draw.text(
                    (pad_l + chart_w + 10, y),
                    _safe_text(_format_diag_value_label(ref_low)),
                    fill=(21, 128, 61),
                    font=font_small,
                    anchor="lm",
                )

        coords: list[tuple[float, float]] = []
        for i, (_date, value, _status) in enumerate(points):
            x = pad_l + (i / (len(points) - 1)) * chart_w
            y = y_for(value)
            coords.append((x, y))
        draw.line(coords, fill=line_stroke, width=8)
        for i, ((x, y), (date, value, status)) in enumerate(zip(coords, points)):
            color = _status_rgb(status, line_stroke)
            r = 12
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color, outline=(255, 255, 255), width=4)
            val_label = _safe_text(_format_diag_value_label(value))
            if unit and len(points) <= 4:
                val_label = f"{val_label} {unit}"
            y_off = -28 if i % 2 == 0 else -56
            draw.text((x, y + y_off), val_label, fill=(15, 23, 42), font=font_label, anchor="mb")
            date_label = _safe_text(_format_diag_date_label(date))
            if len(points) > 5:
                dt = _parse_iso_datetime(date)
                date_label = dt.strftime("%b %y") if dt else date_label
            draw.text((x, height - 36), date_label, fill=(51, 65, 85), font=font_small, anchor="mb")

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _write_diagnostics_charts(
    pdf: FPDF,
    series: list[dict[str, Any]] | None,
    *,
    as_of: str | None = None,
    max_charts: int = 10,
) -> None:
    """Add a Key diagnostics page with sparkline charts."""
    if not series:
        return

    # Prefer multi-point blood trends; keep a modest page count
    ranked = sorted(
        series,
        key=lambda s: (
            0 if (s.get("category") or "") == "blood" else 1,
            0 if int(s.get("point_count") or 0) > 1 else 1,
            -int(s.get("point_count") or 0),
            str(s.get("name") or "").lower(),
        ),
    )[:max_charts]
    if not ranked:
        return

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 116, 144)
    pdf.cell(0, 10, "Key diagnostics", new_x="LMARGIN", new_y="NEXT", align="L")
    if as_of:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, f"As of {_safe_text(as_of)}", new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    _pdf_multiline(
        pdf,
        _safe_text(
            "Blood-test trends use collection / date of service. "
            "Shaded band shows age- and sex-aware reference targets. "
            "Green = on target; yellow = within 10% beyond bound; red = farther."
        ),
        h=4,
    )
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    usable_w = pdf.w - pdf.l_margin - pdf.r_margin
    chart_h_mm = 70
    row_step = chart_h_mm + 10
    strokes = [(14, 116, 144), (8, 145, 178), (180, 83, 9)]

    for index, item in enumerate(ranked):
        if pdf.get_y() + row_step > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(14, 116, 144)
            pdf.cell(0, 8, "Key diagnostics (continued)", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        x = pdf.l_margin
        y = pdf.get_y()
        name = _safe_text(item.get("name") or "Diagnostic")
        unit = _safe_text(item.get("unit") or "")
        status = item.get("status") or (item.get("latest") or {}).get("status")

        png = _sparkline_png_bytes(
            item.get("readings") or [],
            stroke=strokes[index % len(strokes)],
            reference=item.get("reference"),
            series_status=status if isinstance(status, str) else None,
            title=name,
            unit=unit or None,
        )
        if png:
            pdf.image(BytesIO(png), x=x, y=y, w=usable_w, h=chart_h_mm)
        pdf.set_y(y + row_step)

    pdf.ln(2)


def _response_body_for_pdf(analysis: dict[str, Any]) -> str:
    """Full assessment text for PDF, with executive summary removed when possible."""
    response = (analysis.get("response") or "").strip()
    if not response:
        return ""
    stripped = strip_executive_summary_section(response)
    # Prefer stripped body when it still looks like a real assessment.
    if len(stripped) >= max(200, int(len(response) * 0.15)):
        return stripped
    return response


def build_assessment_pdf(
    analysis: dict[str, Any],
    *,
    patient_context: str | None = None,
    catalog: SourceCatalog | None = None,
    diagnostic_series: list[dict[str, Any]] | None = None,
    patient_label: str | None = None,
    patient_subline: str | None = None,
) -> bytes:
    report_timestamp = _format_timestamp(analysis.get("created_at"))
    report_date, report_time = _format_timestamp_parts(analysis.get("created_at"))
    exported_at = _format_timestamp(datetime.now(timezone.utc).isoformat())
    report_type = _analysis_type_label(analysis.get("analysis_type"))
    display_title = _display_title(analysis)
    if display_title and analysis.get("analysis_type") == "query":
        report_type = display_title

    response_for_pdf = _response_body_for_pdf(analysis)
    ref_bundle = build_reference_bundle(
        executive_summary=analysis.get("executive_summary") or "",
        response=response_for_pdf,
        patient_context=patient_context or "",
        catalog=catalog,
    )

    pdf = AssessmentPDF(
        report_date=report_date,
        report_time=report_time,
        report_type=report_type,
        exported_at=exported_at,
        patient_label=patient_label,
        patient_subline=patient_subline,
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
    pdf.ln(4)

    _write_collaboration_block(pdf, analysis)

    response_title = "Full Assessment"
    if analysis.get("analysis_type") == "query":
        response_title = "Full response"

    _write_section_with_references(
        pdf,
        "Executive Summary",
        ref_bundle["sections"]["executive_summary"],
        as_of=report_timestamp,
        include_section_references=False,
    )

    _write_diagnostics_charts(
        pdf,
        diagnostic_series,
        as_of=report_timestamp,
    )

    _write_section_with_references(
        pdf,
        response_title,
        ref_bundle["sections"]["response"],
        as_of=report_timestamp,
        include_section_references=False,
        force_new_page=True,
    )

    patient_section = ref_bundle["sections"]["patient_context"]
    if patient_context and patient_context.strip() and patient_section.get("body", "").strip():
        pdf.add_page()
        _write_section_with_references(
            pdf,
            "Patient Context (Settings)",
            patient_section,
            as_of=report_timestamp,
            include_section_references=False,
        )

    _write_appendix_references(
        pdf,
        _expand_appendix_with_scope_documents(ref_bundle["appendix"], analysis, catalog),
        as_of=report_timestamp,
    )

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def assessment_pdf_filename(
    analysis: dict[str, Any],
    *,
    exported_at: datetime | None = None,
) -> str:
    stamp = _format_filename_stamp(
        (exported_at or datetime.now(timezone.utc)).isoformat()
    )

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


def diagnostics_pdf_filename(
    *,
    patient_label: str | None = None,
    exported_at: datetime | None = None,
) -> str:
    stamp = _format_filename_stamp(
        (exported_at or datetime.now(timezone.utc)).isoformat()
    )
    slug = ""
    if patient_label:
        slug = re.sub(r"[^\w\s-]", "", patient_label.lower())
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")[:36]
    if slug:
        return f"beatit-diagnostics-{slug}-{stamp}.pdf"
    return f"beatit-diagnostics-{stamp}.pdf"


def build_diagnostics_pdf(
    series: list[dict[str, Any]],
    *,
    patient_label: str | None = None,
    patient_subline: str | None = None,
) -> bytes:
    """Diagnostics-only PDF with charts, traffic-light status, and export timestamp."""
    now = datetime.now(timezone.utc)
    exported_at = _format_timestamp(now.isoformat())
    report_date, report_time = _format_timestamp_parts(now.isoformat())

    pdf = AssessmentPDF(
        report_date=report_date,
        report_time=report_time,
        report_type="Diagnostics export",
        exported_at=exported_at,
        patient_label=patient_label,
        patient_subline=patient_subline,
    )
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=28)
    pdf.set_margins(18, 38, 18)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 116, 144)
    title = "Key diagnostics"
    if patient_label:
        title = f"Key diagnostics - {_safe_text(patient_label)}"
    pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT", align="L")

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(80, 80, 80)
    meta = [f"Exported: {exported_at}"]
    if patient_subline:
        meta.append(_safe_text(patient_subline))
    pdf.cell(0, 5, _safe_text(" · ".join(meta)), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    _pdf_multiline(
        pdf,
        _safe_text(
            "Green = on target vs reference. Yellow = within 10% beyond the bound. "
            "Red = farther than 10%. References are approximate age/sex orientation, not medical advice."
        ),
        h=4,
    )
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)

    if not series:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 8, "No diagnostic readings to export.", new_x="LMARGIN", new_y="NEXT")
    else:
        usable_w = pdf.w - pdf.l_margin - pdf.r_margin
        chart_h_mm = 78
        row_step = chart_h_mm + 12
        strokes = [(14, 116, 144), (8, 145, 178), (180, 83, 9)]
        ranked = sorted(
            series,
            key=lambda s: (
                0 if (s.get("category") or "") == "blood" else 1,
                0 if int(s.get("point_count") or 0) > 1 else 1,
                -int(s.get("point_count") or 0),
                str(s.get("name") or "").lower(),
            ),
        )
        for index, item in enumerate(ranked):
            if pdf.get_y() + row_step > pdf.h - pdf.b_margin:
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 13)
                pdf.set_text_color(14, 116, 144)
                pdf.cell(0, 8, "Key diagnostics (continued)", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)

            x = pdf.l_margin
            y = pdf.get_y()
            name = _safe_text(item.get("name") or "Diagnostic")
            unit = _safe_text(item.get("unit") or "")
            latest = item.get("latest") or {}
            status = item.get("status") or latest.get("status")

            # Text summary above chart for quick clinical scan
            latest_val = _format_diag_value_label(latest.get("value"))
            latest_date = _format_diag_date_label(latest.get("recorded_at"))
            unit_bit = f" {unit}" if unit else ""
            status_bit = f"  |  {str(status).capitalize()}" if status in _STATUS_RGB else ""
            ref = item.get("reference") or {}
            ref_bit = f"  |  Ref {ref['label']}" if ref.get("label") else ""
            summary = f"Latest: {latest_val}{unit_bit} on {latest_date}{status_bit}{ref_bit}"

            pdf.set_xy(x, y)
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(usable_w, 7, name, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 11)
            pdf.set_text_color(*_status_rgb(status if isinstance(status, str) else None, (71, 85, 105)))
            pdf.cell(usable_w, 6, _safe_text(summary), new_x="LMARGIN", new_y="NEXT")
            y_chart = pdf.get_y() + 1

            png = _sparkline_png_bytes(
                item.get("readings") or [],
                stroke=strokes[index % len(strokes)],
                reference=item.get("reference"),
                series_status=status if isinstance(status, str) else None,
                unit=unit or None,
            )
            chart_draw_h = chart_h_mm - 14
            if png:
                pdf.image(BytesIO(png), x=x, y=y_chart, w=usable_w, h=chart_draw_h)
            pdf.set_y(y + row_step)

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()
