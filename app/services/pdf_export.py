import math
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter

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
        "\u2265": ">=",
        "\u2264": "<=",
        "\u00b5": "u",
        "\u03bc": "u",
        "\u2212": "-",
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
        orientation: str = "P",
    ):
        super().__init__(orientation=orientation)
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


def _format_diag_date_label(iso: str | None, *, compact: bool = False) -> str:
    raw = str(iso or "")[:10]
    dt = _parse_iso_datetime(raw)
    if not dt:
        return raw or "-"
    if compact:
        # Short numeric form avoids same-month collisions on crowded axes
        return f"{dt.month}/{dt.day}/{str(dt.year)[2:]}"
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _format_diag_date_precise(iso: str | None) -> str:
    """Human date + ISO for doctor reference points (e.g. Jun 10, 2026 · 2026-06-10)."""
    raw = str(iso or "")[:10]
    pretty = _format_diag_date_label(raw, compact=False)
    if not raw or pretty == raw:
        return pretty or "-"
    return f"{pretty} · {raw}"


def _format_diag_points_reference(readings: list[dict[str, Any]] | None) -> str:
    """Compact precise value@date list for PDF under each trend chart."""
    by_day: dict[str, tuple[str, Any]] = {}
    for row in readings or []:
        date = str(row.get("recorded_at") or "")[:10]
        if not date:
            continue
        by_day[date] = (date, row.get("value"))
    bits: list[str] = []
    for date in sorted(by_day):
        _d, value = by_day[date]
        bits.append(f"{date}={_format_diag_value_label(value)}")
    return " · ".join(bits)


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


def _pick_y_axis_ticks(
    vmin: float,
    vmax: float,
    ref_low: float | None = None,
    ref_high: float | None = None,
) -> list[tuple[float, str]]:
    """Return (value, kind) ticks for the chart Y axis; kind is 'axis' or 'ref'."""
    span = max(vmax - vmin, abs(vmax) * 1e-6, 1e-6)
    rough = span / 3.0
    mag = 10 ** math.floor(math.log10(rough)) if rough > 0 else 1.0
    norm = rough / mag
    if norm > 7:
        step = 10 * mag
    elif norm > 3:
        step = 5 * mag
    elif norm > 1.5:
        step = 2 * mag
    else:
        step = mag
    start = math.ceil((vmin - step * 1e-9) / step) * step
    ticks: list[tuple[float, str]] = []

    def add(value: float | None, kind: str) -> None:
        if value is None:
            return
        try:
            n = float(value)
        except (TypeError, ValueError):
            return
        if n != n or n < vmin - span * 0.02 or n > vmax + span * 0.02:
            return
        for i, (existing, ekind) in enumerate(ticks):
            if abs(existing - n) < span * 0.06:
                if kind == "ref" and ekind != "ref":
                    ticks[i] = (existing if ekind == "ref" else n, "ref")
                return
        ticks.append((n, kind))

    v = start
    guard = 0
    while v <= vmax + step * 1e-9 and guard < 24:
        add(v, "axis")
        v += step
        guard += 1
    add(ref_low, "ref")
    add(ref_high, "ref")
    if not ticks:
        add(vmin, "axis")
        add(vmax, "axis")
    ticks.sort(key=lambda t: t[0])
    return ticks


_STATUS_RGB = {
    "green": (21, 128, 61),
    "yellow": (202, 138, 4),
    "red": (185, 28, 28),
}


def _status_rgb(status: str | None, fallback: tuple[int, int, int] = (14, 116, 144)) -> tuple[int, int, int]:
    if not status:
        return fallback
    return _STATUS_RGB.get(str(status).lower(), fallback)


def _status_severity(status: str | None) -> int:
    key = str(status or "").lower()
    if key == "red":
        return 3
    if key == "yellow":
        return 2
    if key == "green":
        return 1
    return 0


def _worse_status(a: str | None, b: str | None) -> str | None:
    if _status_severity(a) >= _status_severity(b):
        return a or b
    return b or a


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


def _load_chart_fonts(*, compact: bool = False) -> tuple[Any, Any, Any, Any]:
    """Return (title, value, label, small) Pillow fonts sized for print clarity."""
    from PIL import ImageFont

    regular, bold = _chart_font_paths()
    # Compact = denser multi-chart PDF pages
    sizes = (36, 64, 28, 24) if compact else (48, 96, 40, 32)
    try:
        if bold and regular:
            return (
                ImageFont.truetype(str(bold), sizes[0]),
                ImageFont.truetype(str(bold), sizes[1]),
                ImageFont.truetype(str(bold), sizes[2]),
                ImageFont.truetype(str(regular), sizes[3]),
            )
        if regular:
            return (
                ImageFont.truetype(str(regular), sizes[0]),
                ImageFont.truetype(str(regular), sizes[1]),
                ImageFont.truetype(str(regular), sizes[2]),
                ImageFont.truetype(str(regular), sizes[3]),
            )
    except OSError:
        pass
    fallback = ImageFont.load_default()
    return fallback, fallback, fallback, fallback


def _sparkline_png_bytes(
    readings: list[dict[str, Any]],
    *,
    width: int = 1600,
    height: int = 360,
    stroke: tuple[int, int, int] = (14, 116, 144),
    reference: dict[str, Any] | None = None,
    series_status: str | None = None,
    title: str | None = None,
    unit: str | None = None,
    milestones: list[dict[str, Any]] | None = None,
) -> bytes | None:
    """Render a print-quality diagnostic chart PNG for embedding in the PDF."""
    from app.services.medication_events import filter_events_for_range

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

    # One point per calendar day (last reading that day wins)
    by_day: dict[str, tuple[str, float, str | None]] = {}
    for date, value, status in points:
        by_day[date] = (date, value, status)
    points = [by_day[k] for k in sorted(by_day)]

    # Single-point cards are rendered as text rows in the PDF — no PNG needed.
    if len(points) == 1:
        return None

    from PIL import Image, ImageDraw

    line_stroke = _status_rgb(series_status or points[-1][2], stroke)
    font_title, _font_value, font_label, font_small = _load_chart_fonts(compact=True)

    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)

    # Room on the left for Y-axis; extra bottom for non-overlapping dates + drop lines
    pad_l, pad_r, pad_t, pad_b = 78, 48, 20, 64
    if title:
        draw.text((pad_l, 6), _safe_text(title), fill=(15, 23, 42, 255), font=font_title, anchor="lt")
        pad_t = 44

    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    ref_low = None
    ref_high = None
    direction = "range"
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
        direction = str(reference.get("direction") or "range").lower()

    values = [p[1] for p in points]
    vmin, vmax = min(values), max(values)
    if ref_low is not None:
        vmin = min(vmin, ref_low)
    if ref_high is not None:
        vmax = max(vmax, ref_high)
    # Expand axis so one-sided targets get a visible green zone (not just a bound line)
    if direction == "lower_better" and ref_high is not None:
        vmin = min(vmin, ref_high - max(abs(ref_high) * 0.25, 0.2))
    if direction == "higher_better" and ref_low is not None:
        vmax = max(vmax, ref_low + max(abs(ref_low) * 0.25, 0.2))
    # Fit the chart to the data (+ reference bounds) with modest padding so the
    # trend fills the plot — never force a 0 baseline that collapses the line.
    y_pad = (vmax - vmin) * 0.18 or max(abs(vmax) * 0.08, 0.15)
    vmin -= y_pad * 0.55
    vmax += y_pad * 0.55
    if abs(vmax - vmin) < 1e-9:
        vmax = vmin + 1.0
    y_span = vmax - vmin

    def y_for(val: float) -> float:
        return pad_t + chart_h - ((val - vmin) / y_span) * chart_h

    def _day(d: str) -> float:
        try:
            return float(datetime.fromisoformat(d[:10]).toordinal())
        except ValueError:
            return 0.0

    day_vals = [_day(p[0]) for p in points]
    t_min, t_max = min(day_vals), max(day_vals)
    read_min, read_max = t_min, t_max
    # Keep med starts that land shortly after the last lab on the timeline
    range_events = filter_events_for_range(
        milestones,
        start=points[0][0],
        end=points[-1][0],
        pad_days=60,
    )
    pre_days: list[float] = []
    post_days: list[float] = []
    for ev in range_events:
        d = str(ev.get("date") or "")[:10]
        if not d:
            continue
        day = _day(d)
        if not day:
            continue
        if day < read_min:
            pre_days.append(day)
        if day > read_max:
            post_days.append(day)
    read_span = read_max - read_min or 1.0
    if pre_days:
        t_min = min(min(pre_days), read_min - max(read_span * 0.18, 40.0))
    if post_days:
        # Visual headroom so a start a few days after the last lab isn't glued to it
        t_max = max(max(post_days), read_max + max(read_span * 0.22, 50.0))
    t_span = (t_max - t_min) or 1.0
    edge_inset = min(56, chart_w * 0.07)
    usable = max(chart_w - 2 * edge_inset, 1)

    def x_for_day(day: float) -> float:
        return pad_l + edge_inset + ((day - t_min) / t_span) * usable

    plot_top = float(pad_t)
    plot_bottom = float(pad_t + chart_h)

    draw.rectangle(
        (pad_l, pad_t, pad_l + chart_w, pad_t + chart_h),
        outline=(148, 163, 184, 255),
        width=2,
    )

    # 1) Green target zone — semi-transparent so grid / lines stay readable through it
    if ref_low is not None or ref_high is not None:
        band_y1: float | None = None
        band_y2: float | None = None
        if direction == "lower_better" and ref_high is not None:
            band_y1 = y_for(ref_high)
            band_y2 = plot_bottom
        elif direction == "higher_better" and ref_low is not None:
            band_y1 = plot_top
            band_y2 = y_for(ref_low)
        elif ref_low is not None and ref_high is not None:
            band_y1 = y_for(ref_high)
            band_y2 = y_for(ref_low)
        elif ref_high is not None:
            band_y1 = y_for(ref_high)
            band_y2 = plot_bottom
        elif ref_low is not None:
            band_y1 = plot_top
            band_y2 = y_for(ref_low)
        if band_y1 is not None and band_y2 is not None:
            y1 = max(plot_top, min(plot_bottom, min(band_y1, band_y2)))
            y2 = max(plot_top, min(plot_bottom, max(band_y1, band_y2)))
            if y2 - y1 >= 2:
                band_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                band_draw = ImageDraw.Draw(band_layer)
                band_draw.rectangle(
                    (pad_l + 2, y1, pad_l + chart_w - 2, y2),
                    fill=(21, 128, 61, 24),
                )
                img = Image.alpha_composite(img, band_layer)
                draw = ImageDraw.Draw(img)

    # 2) Y-axis scale (+ reference bound lines)
    y_ticks = _pick_y_axis_ticks(vmin, vmax, ref_low, ref_high)
    for tick_val, tick_kind in y_ticks:
        y = y_for(tick_val)
        if y < plot_top - 2 or y > plot_bottom + 2:
            continue
        is_ref = tick_kind == "ref"
        color = (21, 128, 61, 210) if is_ref else (148, 163, 184, 160)
        if is_ref:
            draw.line((pad_l, y, pad_l + chart_w, y), fill=color, width=2)
        else:
            dash = 8
            xx = pad_l + 2
            while xx < pad_l + chart_w - 2:
                draw.line(
                    (xx, y, min(xx + dash, pad_l + chart_w - 2), y),
                    fill=(203, 213, 225, 140),
                    width=1,
                )
                xx += dash * 2
        draw.text(
            (pad_l - 8, y),
            _safe_text(_format_diag_value_label(tick_val)),
            fill=(21, 128, 61, 255) if is_ref else (71, 85, 105, 255),
            font=font_small,
            anchor="rm",
        )

    n = len(points)
    coords: list[tuple[float, float, str | None]] = []
    for date, value, status in points:
        x = x_for_day(_day(date))
        y = y_for(value)
        coords.append((x, y, status))

    # 3) Very light drop-lines from each reading down through the plot (date↔point mapping)
    for x, y, _status in coords:
        draw.line(
            (x, y + 8, x, plot_bottom),
            fill=(100, 116, 139, 120),
            width=1,
        )

    # 4) Trend segments + dots + value labels
    for i in range(len(coords) - 1):
        x0, y0, s0 = coords[i]
        x1, y1, s1 = coords[i + 1]
        seg_rgb = _status_rgb(_worse_status(s0, s1), line_stroke)
        draw.line((x0, y0, x1, y1), fill=(*seg_rgb, 255), width=5)

    label_every = 1 if n <= 5 else 2
    for i, ((x, y, _st), (_date, value, status)) in enumerate(zip(coords, points)):
        color = _status_rgb(status, line_stroke)
        r = 8
        draw.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=(*color, 255),
            outline=(255, 255, 255, 255),
            width=2,
        )
        if i % label_every == 0 or i in (0, n - 1):
            val_label = _safe_text(_format_diag_value_label(value))
            if y - pad_t < 36:
                y_off = 18
                anchor = "mt"
            else:
                y_off = -12 if (i // label_every) % 2 == 0 else -26
                anchor = "mb"
            x_off = 0
            if i == n - 1 and (
                (ref_high is not None and abs(value - ref_high) < y_span * 0.03)
                or (ref_low is not None and abs(value - ref_low) < y_span * 0.03)
            ):
                x_off = -14
                anchor = "mb"
                y_off = -12
            draw.text(
                (x + x_off, y + y_off),
                val_label,
                fill=(15, 23, 42, 255),
                font=font_label,
                anchor=anchor,
            )

    # 5) Milestone markers in the foreground (above band + trend)
    marker_fill = (100, 116, 139)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for ev in range_events:
        d = str(ev.get("date") or "")[:10]
        if not d:
            continue
        by_day.setdefault(d, []).append(ev)
    milestone_marks: list[tuple[float, tuple[int, int, int]]] = []
    for _mi, (d, day_events) in enumerate(list(by_day.items())[:6]):
        try:
            day = float(datetime.fromisoformat(d).toordinal())
        except ValueError:
            continue
        if day < t_min or day > t_max:
            continue
        x = x_for_day(day)
        hex_color = str((day_events[0] or {}).get("color") or "")
        try:
            if hex_color.startswith("#") and len(hex_color) >= 7:
                fill = (
                    int(hex_color[1:3], 16),
                    int(hex_color[3:5], 16),
                    int(hex_color[5:7], 16),
                )
            else:
                fill = marker_fill
        except ValueError:
            fill = marker_fill
        milestone_marks.append((x, fill))

    def _draw_milestones() -> None:
        for x, fill in milestone_marks:
            y0, y1 = pad_t + 2, pad_t + chart_h - 2
            # Soft white underlay so dashes stay visible over the green zone
            dash = 10
            yy = y0
            while yy < y1:
                draw.line((x, yy, x, min(yy + dash, y1)), fill=(255, 255, 255, 200), width=5)
                yy += dash * 2
            yy = y0
            while yy < y1:
                draw.line((x, yy, x, min(yy + dash, y1)), fill=(*fill, 255), width=3)
                yy += dash * 2
            tip = 9
            draw.polygon(
                [(x, y0 + tip), (x - tip, y0), (x + tip, y0)],
                fill=(255, 255, 255, 255),
            )
            draw.polygon(
                [(x, y0 + tip - 1), (x - tip + 1, y0 + 1), (x + tip - 1, y0 + 1)],
                fill=(*fill, 255),
            )

    _draw_milestones()

    # 6) Axis dates — non-overlapping, with light connectors from plot to label
    axis_y_top = height - 36
    axis_y_bot = height - 16
    n_pts = len(coords)
    # Prefer short labels; precise ISO stays in the Points line under the chart
    use_compact = n_pts > 2
    candidates: list[tuple[int, float, str, float, float, str]] = []
    for i, ((x, _y, _st), (date, _value, _status)) in enumerate(zip(coords, points)):
        date_label = _safe_text(
            _format_diag_date_label(date, compact=use_compact)
            if use_compact
            else _format_diag_date_label(date, compact=False)
        )
        if not date_label:
            continue
        est_w = max(22, len(date_label) * (4.8 if use_compact else 5.4))
        if i == 0:
            anchor = "lt"
            left, right = x, x + est_w
        elif i == n_pts - 1:
            anchor = "rt"
            left, right = x - est_w, x
        else:
            anchor = "mt"
            left, right = x - est_w / 2, x + est_w / 2
        candidates.append((i, x, date_label, left, right, anchor))

    kept: list[tuple[int, float, str, float, float, str, int]] = []

    def _fits(left: float, right: float, row: int) -> bool:
        for _ki, _kx, _kl, kleft, kright, _ka, krow in kept:
            if krow != row:
                continue
            if left < kright + 12 and right > kleft - 12:
                return False
        return True

    # Mandatory first + last (stagger if needed), then pack middles that fit — drop the rest
    for cand in candidates:
        i, x, date_label, left, right, anchor = cand
        if i not in (0, n_pts - 1):
            continue
        placed = False
        for try_row in (0, 1):
            if _fits(left, right, try_row):
                kept.append((*cand, try_row))
                placed = True
                break
        if not placed:
            kept.append((*cand, 1 if any(k[6] == 0 for k in kept) else 0))

    for cand in candidates:
        i, x, date_label, left, right, anchor = cand
        if i in (0, n_pts - 1):
            continue
        for try_row in (i % 2, 1 - (i % 2)):
            if _fits(left, right, try_row):
                kept.append((*cand, try_row))
                break
    kept.sort(key=lambda t: (t[6], t[0]))

    for i, x, date_label, _left, _right, anchor, row in kept:
        axis_y = axis_y_top if row == 0 else axis_y_bot
        # Light connector from plot bottom to the date label
        draw.line(
            (x, plot_bottom, x, axis_y - 4),
            fill=(100, 116, 139, 130),
            width=1,
        )
        draw.text(
            (x, axis_y),
            date_label,
            fill=(51, 65, 85, 255),
            font=font_small,
            anchor=anchor,
        )

    # Redraw milestones last so they stay above drop-lines / labels near the frame
    _draw_milestones()

    buf = BytesIO()
    # Flatten to RGB for smaller PDFs / broader compatibility
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _write_single_reading_rows(
    pdf: FPDF,
    items: list[dict[str, Any]],
    *,
    usable_w: float,
) -> None:
    """Compact two-column table for one-off lab values (no chart art)."""
    if not items:
        return
    col_gap = 3.5
    col_w = (usable_w - col_gap) / 2
    row_h = 7.8
    left_x = pdf.l_margin
    right_x = pdf.l_margin + col_w + col_gap
    # Fixed columns so dates stay vertically aligned regardless of value length.
    tick_w = 2.8
    date_w = 18.0
    value_w = 24.0

    for i in range(0, len(items), 2):
        pair = items[i : i + 2]
        has_ref = any((p.get("reference") or {}).get("label") for p in pair)
        pair_h = row_h + (1.8 if has_ref else 0)
        if pdf.get_y() + pair_h + 2.5 > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(14, 116, 144)
            pdf.cell(0, 6, "Other readings (continued)", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)

        y = pdf.get_y()
        for col, item in enumerate(pair):
            x = left_x if col == 0 else right_x
            name = _safe_text(item.get("name") or "Diagnostic")
            unit = _safe_text(item.get("unit") or "")
            latest = item.get("latest") or {}
            status = item.get("status") or latest.get("status")
            val = _format_diag_value_label(latest.get("value"))
            date = str(latest.get("recorded_at") or "")[:10] or _format_diag_date_label(
                latest.get("recorded_at"), compact=True
            )
            unit_bit = f" {unit}" if unit else ""
            value_text = f"{val}{unit_bit}"

            # Status tick
            if status in _STATUS_RGB:
                pdf.set_fill_color(*_status_rgb(str(status)))
                pdf.rect(x, y + 1.4, 2.0, 2.0, style="F")
                text_x = x + tick_w
                text_w = col_w - tick_w
            else:
                text_x = x
                text_w = col_w

            name_w = max(text_w - value_w - date_w, 12.0)
            value_x = text_x + name_w
            date_x = value_x + value_w

            pdf.set_xy(text_x, y)
            pdf.set_font("Helvetica", "B", 7.5)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(name_w, 3.4, name[:28], new_x="LMARGIN", new_y="TOP")

            pdf.set_xy(value_x, y)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.set_text_color(*_status_rgb(status if isinstance(status, str) else None, (51, 65, 85)))
            pdf.cell(value_w - 1.0, 3.4, _safe_text(value_text)[:18], align="R", new_x="LMARGIN", new_y="TOP")

            pdf.set_xy(date_x, y)
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(date_w, 3.4, _safe_text(date)[:10], align="R", new_x="LMARGIN", new_y="TOP")

            ref = item.get("reference") or {}
            if ref.get("label"):
                pdf.set_xy(text_x, y + 3.4)
                pdf.set_font("Helvetica", "", 6)
                pdf.set_text_color(120, 120, 120)
                pdf.cell(text_w, 2.6, _safe_text(f"Ref {ref['label']}")[:48], new_x="LMARGIN", new_y="TOP")

        pdf.set_y(y + pair_h)
        # Thin separator under every result row
        pdf.set_draw_color(226, 232, 240)
        pdf.set_line_width(0.2)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(1.1)
        pdf.set_line_width(0.2)


def _write_diagnostics_charts(
    pdf: FPDF,
    series: list[dict[str, Any]] | None,
    *,
    as_of: str | None = None,
    max_charts: int = 10,
    milestones: list[dict[str, Any]] | None = None,
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
            "Green shaded zone = on-target range (or the safe side of a one-sided bound). "
            "Trend line segments follow reading status: green / yellow / red. "
            "Dashed vertical markers show medication starts, dose changes, and stops."
        ),
        h=4,
    )
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)

    usable_w = pdf.w - pdf.l_margin - pdf.r_margin
    chart_h_mm = 50
    gap_after_mm = 10
    row_step = chart_h_mm + gap_after_mm
    strokes = [(14, 116, 144), (8, 145, 178), (180, 83, 9)]

    for index, item in enumerate(ranked):
        readings = item.get("readings") or []
        # Sparkline PNG is trends-only; skip one-offs in the assessment digest.
        if len(readings) <= 1:
            continue
        if pdf.get_y() + row_step > pdf.h - pdf.b_margin:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(14, 116, 144)
            pdf.cell(0, 7, "Key diagnostics (continued)", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        x = pdf.l_margin
        y = pdf.get_y()
        name = _safe_text(item.get("name") or "Diagnostic")
        unit = _safe_text(item.get("unit") or "")
        status = item.get("status") or (item.get("latest") or {}).get("status")

        png = _sparkline_png_bytes(
            readings,
            stroke=strokes[index % len(strokes)],
            reference=item.get("reference"),
            series_status=status if isinstance(status, str) else None,
            title=name,
            unit=unit or None,
            height=420,
            milestones=milestones,
        )
        if png:
            pdf.image(BytesIO(png), x=x, y=y, w=usable_w, h=chart_h_mm)
            pdf.set_y(y + chart_h_mm + gap_after_mm)
            # Soft separator between metrics
            pdf.set_draw_color(226, 232, 240)
            pdf.set_line_width(0.3)
            sep_y = pdf.get_y() - gap_after_mm / 2
            pdf.line(pdf.l_margin, sep_y, pdf.w - pdf.r_margin, sep_y)

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
    milestones: list[dict[str, Any]] | None = None,
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
        milestones=milestones,
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


def merge_pdf_documents(parts: list[bytes]) -> bytes:
    """Concatenate PDF byte blobs into a single document (order preserved)."""
    writer = PdfWriter()
    for raw in parts:
        if not raw:
            continue
        reader = PdfReader(BytesIO(raw))
        for page in reader.pages:
            writer.add_page(page)
    if not writer.pages:
        raise ValueError("No PDF pages to merge")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def patient_bundle_pdf_filename(
    *,
    patient_label: str | None = None,
    parts: list[str] | None = None,
    exported_at: datetime | None = None,
) -> str:
    stamp = _format_filename_stamp(
        (exported_at or datetime.now(timezone.utc)).isoformat()
    )
    slug = ""
    if patient_label:
        slug = re.sub(r"[^\w\s-]", "", patient_label.lower())
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")[:36]
    part_key = "-".join(parts) if parts else "bundle"
    if len(part_key) > 40:
        part_key = "bundle"
    if slug:
        return f"beatit-export-{part_key}-{slug}-{stamp}.pdf"
    return f"beatit-export-{part_key}-{stamp}.pdf"


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
    milestones: list[dict[str, Any]] | None = None,
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
    pdf.set_auto_page_break(auto=True, margin=26)
    pdf.set_margins(14, 36, 14)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(14, 116, 144)
    title = "Key diagnostics"
    if patient_label:
        title = f"Key diagnostics - {_safe_text(patient_label)}"
    pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT", align="L")

    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(80, 80, 80)
    meta = [f"Exported: {exported_at}"]
    if patient_subline:
        meta.append(_safe_text(patient_subline))
    pdf.cell(0, 4, _safe_text(" · ".join(meta)), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8)
    legend = (
        "Green shaded zone = target. Line segments follow each reading (green / yellow / red). "
        "Gray = no reference yet. Dashed markers = med or lifestyle milestones. "
        "Exact collection dates (YYYY-MM-DD) are listed under each trend. "
        "Trend charts first; single readings in the compact table below."
    )
    if milestones:
        seen: list[str] = []
        for ev in milestones:
            lab = str(ev.get("label") or "").strip()
            if lab and lab not in seen:
                seen.append(lab)
            if len(seen) >= 3:
                break
        if seen:
            legend += " Markers: " + " · ".join(seen) + "."
    _pdf_multiline(pdf, _safe_text(legend), h=3.6)
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3.5)

    if not series:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 8, "No diagnostic readings to export.", new_x="LMARGIN", new_y="NEXT")
    else:
        usable_w = pdf.w - pdf.l_margin - pdf.r_margin
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
        trends: list[dict[str, Any]] = []
        singles: list[dict[str, Any]] = []
        for item in ranked:
            readings = item.get("readings") or []
            count = len(readings) or int(item.get("point_count") or 0)
            if count > 1:
                trends.append(item)
            else:
                singles.append(item)

        chart_h_mm = 58
        for index, item in enumerate(trends):
            points_line = _format_diag_points_reference(item.get("readings") or [])
            # title + pre-gap + chart + points line + post-gap (more air between metrics)
            points_h = 7.0 if points_line else 0.0
            gap_after = 8.0
            block_h = chart_h_mm + 16 + points_h + gap_after
            if pdf.get_y() + block_h > pdf.h - pdf.b_margin:
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 11)
                pdf.set_text_color(14, 116, 144)
                pdf.cell(0, 6, "Trend charts (continued)", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(4)

            x = pdf.l_margin
            name = _safe_text(item.get("name") or "Diagnostic")
            unit = _safe_text(item.get("unit") or "")
            latest = item.get("latest") or {}
            status = item.get("status") or latest.get("status")
            latest_val = _format_diag_value_label(latest.get("value"))
            latest_date = _format_diag_date_precise(latest.get("recorded_at"))
            unit_bit = f" {unit}" if unit else ""
            status_bit = f" | {str(status).capitalize()}" if status in _STATUS_RGB else ""
            ref = item.get("reference") or {}
            ref_bit = f" | Ref {ref['label']}" if ref.get("label") else ""
            summary = f"{latest_val}{unit_bit} · {latest_date}{status_bit}{ref_bit}"

            pdf.set_x(x)
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(usable_w * 0.40, 5.0, name, new_x="END", new_y="TOP")
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_status_rgb(status if isinstance(status, str) else None, (71, 85, 105)))
            pdf.cell(usable_w * 0.60, 5.0, _safe_text(summary), align="R", new_x="LMARGIN", new_y="NEXT")
            y_chart = pdf.get_y() + 2.0

            png = _sparkline_png_bytes(
                item.get("readings") or [],
                stroke=strokes[index % len(strokes)],
                reference=item.get("reference"),
                series_status=status if isinstance(status, str) else None,
                unit=unit or None,
                height=480,
                milestones=milestones,
            )
            if png:
                pdf.image(BytesIO(png), x=x, y=y_chart, w=usable_w, h=chart_h_mm)
            pdf.set_y(y_chart + chart_h_mm + 2.5)
            if points_line:
                pdf.set_font("Helvetica", "", 6.5)
                pdf.set_text_color(71, 85, 105)
                _pdf_multiline(
                    pdf,
                    _safe_text(f"Points (collection date=value): {points_line}"),
                    h=3.2,
                )
                pdf.ln(2.0)
            else:
                pdf.ln(2.0)
            # Soft separator between metrics
            pdf.set_draw_color(226, 232, 240)
            pdf.set_line_width(0.3)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(gap_after)

        if singles:
            if pdf.get_y() + 20 > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(14, 116, 144)
            pdf.cell(0, 6, "Other readings", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(100, 100, 100)
            pdf.cell(
                0,
                3.5,
                "Single collection points (no trend line yet)",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.ln(1)
            _write_single_reading_rows(pdf, singles, usable_w=usable_w)

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


def coverage_pdf_filename(
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
        return f"beatit-coverage-{slug}-{stamp}.pdf"
    return f"beatit-coverage-{stamp}.pdf"


def _format_coverage_doc_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        raw = str(iso)
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        eastern = dt.astimezone(EASTERN)
        return eastern.strftime("%b %d, %Y").replace(" 0", " ")
    except ValueError:
        return str(iso)[:10]


def build_document_coverage_pdf(
    coverage: dict[str, Any],
    *,
    patient_label: str | None = None,
    patient_subline: str | None = None,
) -> bytes:
    """Documentation coverage / inventory PDF — large type, generous spacing."""
    now = datetime.now(timezone.utc)
    exported_at = _format_timestamp(now.isoformat())
    report_date, report_time = _format_timestamp_parts(now.isoformat())

    pdf = AssessmentPDF(
        report_date=report_date,
        report_time=report_time,
        report_type="Documentation coverage",
        exported_at=exported_at,
        patient_label=patient_label,
        patient_subline=patient_subline,
    )
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=28)
    pdf.set_margins(16, 38, 16)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(14, 116, 144)
    title = "Documentation coverage"
    if patient_label:
        title = f"Documentation coverage - {_safe_text(patient_label)}"
    pdf.cell(0, 10, _safe_text(title), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(70, 70, 70)
    total = int(coverage.get("total") or 0)
    present = int(coverage.get("present_count") or 0)
    missing = int(coverage.get("missing_count") or 0)
    meta = [
        f"Exported: {exported_at}",
        f"{total} document{'s' if total != 1 else ''}",
        f"{present} coverage areas present",
        f"{missing} missing",
    ]
    _pdf_multiline(pdf, _safe_text(" · ".join(meta)), h=5.5)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)
    _pdf_multiline(
        pdf,
        "Inventory of stored documentation by clinical type and upload date. "
        "Use this to see what is present, what is missing, and what needs attention.",
        h=5.5,
    )
    pdf.ln(2)
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)

    # Checklist
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 8, "Coverage checklist", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    usable_w = pdf.w - pdf.l_margin - pdf.r_margin
    for item in coverage.get("checklist") or []:
        status = "Present" if item.get("present") else "MISSING"
        count = int(item.get("count") or 0)
        label = str(item.get("label") or item.get("id") or "")
        pdf.set_font("Helvetica", "B", 12)
        if item.get("present"):
            pdf.set_text_color(21, 128, 61)
        else:
            pdf.set_text_color(185, 28, 28)
        pdf.cell(usable_w * 0.26, 7, _safe_text(status), new_x="END", new_y="TOP")
        pdf.set_text_color(30, 30, 30)
        pdf.set_font("Helvetica", "", 12)
        pdf.cell(
            0,
            7,
            _safe_text(f"{label} ({count})" if count else label),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(1.5)
    pdf.ln(5)

    # Attention
    attn = coverage.get("attention") or {}
    counts = attn.get("counts") or {}
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 8, "Needs attention", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    sections = [
        ("needs_ocr", "Needs OCR"),
        ("flagged", "Flagged"),
        ("file_missing", "Original file missing"),
        ("unclassified_pdf", "Unclassified PDFs"),
    ]
    any_attention = False
    for key, heading in sections:
        items = attn.get(key) or []
        n = int(counts.get(key) or len(items))
        if not n:
            continue
        any_attention = True
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(146, 64, 14)
        pdf.cell(0, 7, _safe_text(f"{heading} ({n})"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(40, 40, 40)
        for row in items[:40]:
            name = str(row.get("display_name") or row.get("title") or "Untitled")
            case = str(row.get("case_label") or "").strip()
            when = _format_coverage_doc_date(row.get("created_at"))
            bit = f"- {name}"
            if case:
                bit += f"  ·  {case}"
            bit += f"  ·  {when}"
            _pdf_multiline(pdf, _safe_text(bit), h=5.5)
            pdf.ln(0.8)
        if len(items) > 40:
            pdf.set_font("Helvetica", "I", 11)
            pdf.cell(
                0,
                6,
                _safe_text(f"... and {len(items) - 40} more"),
                new_x="LMARGIN",
                new_y="NEXT",
            )
        pdf.ln(3)
    if not any_attention:
        pdf.set_font("Helvetica", "I", 12)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 7, "Nothing flagged for attention.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Inventory by type
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 8, "Inventory by type", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    for group in coverage.get("by_type") or []:
        if pdf.get_y() > pdf.h - 48:
            pdf.add_page()
        label = str(group.get("label") or group.get("id") or "")
        count = int(group.get("count") or 0)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(14, 116, 144)
        pdf.cell(0, 7, _safe_text(f"{label} ({count})"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(40, 40, 40)
        for row in group.get("documents") or []:
            name = str(row.get("display_name") or "Untitled")
            case = str(row.get("case_label") or "").strip()
            when = _format_coverage_doc_date(row.get("created_at"))
            chips: list[str] = []
            if row.get("needs_ocr"):
                chips.append("OCR")
            if row.get("flagged"):
                chips.append("Flagged")
            if row.get("file_missing"):
                chips.append("File missing")
            bit = f"- {name}  ·  {when}"
            if case:
                bit += f"  ·  {case}"
            if chips:
                bit += f"  ·  [{', '.join(chips)}]"
            _pdf_multiline(pdf, _safe_text(bit), h=5.5)
            pdf.ln(0.8)
        imaging_n = int(
            (group.get("imaging_collapsed") or {}).get("count")
            or group.get("imaging_count")
            or 0
        )
        if imaging_n:
            pdf.set_font("Helvetica", "I", 11)
            pdf.set_text_color(100, 100, 100)
            _pdf_multiline(
                pdf,
                _safe_text(
                    f"- {imaging_n} DICOM / imaging slices (counted; not listed individually)"
                ),
                h=5.5,
            )
            pdf.set_font("Helvetica", "", 11)
            pdf.set_text_color(40, 40, 40)
        pdf.ln(4)

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


MEDICATION_EXPORT_SCOPES = frozenset({"rx", "non_rx", "history", "all"})

_MED_SCOPE_LABELS = {
    "rx": "Prescriptions (active)",
    "non_rx": "Non-prescription (active)",
    "history": "History / stopped",
    "all": "All medications",
}


def normalize_medication_export_scope(raw: str | None) -> str:
    value = (raw or "all").strip().lower().replace("-", "_")
    aliases = {
        "prescription": "rx",
        "prescriptions": "rx",
        "otc": "non_rx",
        "nonrx": "non_rx",
        "non_prescription": "non_rx",
        "remedy": "non_rx",
        "remedies": "non_rx",
        "stopped": "history",
        "past": "history",
    }
    value = aliases.get(value, value)
    if value not in MEDICATION_EXPORT_SCOPES:
        raise ValueError("scope must be rx, non_rx, history, or all")
    return value


def medication_export_scope_label(scope: str) -> str:
    return _MED_SCOPE_LABELS.get(normalize_medication_export_scope(scope), "Medications")


def filter_medications_for_export(
    medications: list[dict[str, Any]] | None,
    scope: str,
) -> list[dict[str, Any]]:
    """Filter patient medications for PDF export by Rx / non-Rx / history / all."""
    scope_key = normalize_medication_export_scope(scope)
    rows = [m for m in (medications or []) if isinstance(m, dict) and (m.get("name") or "").strip()]

    def is_active(m: dict[str, Any]) -> bool:
        return (m.get("status") or "active") != "stopped"

    def is_rx(m: dict[str, Any]) -> bool:
        return (m.get("category") or "prescription") == "prescription"

    if scope_key == "rx":
        filtered = [m for m in rows if is_active(m) and is_rx(m)]
    elif scope_key == "non_rx":
        filtered = [m for m in rows if is_active(m) and not is_rx(m)]
    elif scope_key == "history":
        filtered = [m for m in rows if not is_active(m)]
    else:
        filtered = list(rows)

    def sort_key(m: dict[str, Any]) -> tuple:
        active = 0 if is_active(m) else 1
        cat = str(m.get("category") or "prescription")
        cat_rank = 0 if cat == "prescription" else 1 if cat == "otc" else 2
        name = str(m.get("name") or "").lower()
        return (active, cat_rank, name)

    return sorted(filtered, key=sort_key)


def _format_med_export_date(raw: str | None) -> str:
    if not raw:
        return "—"
    text = str(raw).strip()[:10]
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text or "—"
    return dt.strftime("%b %d, %Y").replace(" 0", " ")


def _med_category_label(category: str | None) -> str:
    cat = (category or "prescription").strip().lower()
    if cat == "otc":
        return "OTC"
    if cat == "remedy":
        return "Remedy"
    return "Rx"


def _med_dose_line(med: dict[str, Any]) -> str:
    dosage = (med.get("dosage") or "").strip()
    frequency = (med.get("frequency") or "").strip()
    if dosage and frequency:
        return f"{dosage} · {frequency}"
    return dosage or frequency or "—"


def _med_conditions_line(med: dict[str, Any]) -> str:
    conditions = med.get("conditions") or []
    if isinstance(conditions, str):
        return conditions.strip() or "—"
    if isinstance(conditions, list):
        parts = [str(c).strip() for c in conditions if str(c).strip()]
        return ", ".join(parts) if parts else "—"
    return "—"


def medications_pdf_filename(
    *,
    patient_label: str | None = None,
    scope: str = "all",
    exported_at: datetime | None = None,
) -> str:
    stamp = _format_filename_stamp(
        (exported_at or datetime.now(timezone.utc)).isoformat()
    )
    scope_key = normalize_medication_export_scope(scope)
    slug = ""
    if patient_label:
        slug = re.sub(r"[^\w\s-]", "", patient_label.lower())
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")[:36]
    if slug:
        return f"beatit-medications-{scope_key}-{slug}-{stamp}.pdf"
    return f"beatit-medications-{scope_key}-{stamp}.pdf"


def _estimate_med_row_height(pdf: FPDF, cells: list[tuple[float, str]], line_h: float = 3.6) -> float:
    pdf.set_font("Helvetica", "", 8)
    max_lines = 1
    for width, text in cells:
        cleaned = _break_long_words(_safe_text(text or "—"), limit=max(12, int(width * 1.6)))
        lines = pdf.multi_cell(width, line_h, cleaned, dry_run=True, output="LINES")
        max_lines = max(max_lines, len(lines) or 1)
    return max(line_h * max_lines + 2.2, 7.0)


def _write_med_table_header(pdf: FPDF, cols: list[tuple[str, float]], row_h: float = 7.0) -> None:
    x0 = pdf.l_margin
    y0 = pdf.get_y()
    pdf.set_fill_color(14, 116, 144)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_draw_color(10, 90, 112)
    x = x0
    for label, width in cols:
        pdf.set_xy(x, y0)
        pdf.rect(x, y0, width, row_h, style="DF")
        pdf.set_xy(x + 1.2, y0 + 1.6)
        pdf.cell(width - 2.4, 4, _safe_text(label), align="L")
        x += width
    pdf.set_y(y0 + row_h)


def _write_med_table_row(
    pdf: FPDF,
    cols: list[tuple[str, float]],
    values: list[str],
    *,
    fill: bool,
    row_h: float,
) -> None:
    x0 = pdf.l_margin
    y0 = pdf.get_y()
    if fill:
        pdf.set_fill_color(240, 249, 255)
        pdf.rect(x0, y0, sum(w for _, w in cols), row_h, style="F")
    pdf.set_draw_color(196, 214, 224)
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "", 8)
    x = x0
    line_h = 3.6
    for (_label, width), value in zip(cols, values):
        pdf.rect(x, y0, width, row_h, style="D")
        cleaned = _break_long_words(_safe_text(value or "—"), limit=max(12, int(width * 1.6)))
        pdf.set_xy(x + 1.2, y0 + 1.2)
        pdf.multi_cell(width - 2.4, line_h, cleaned, new_x="RIGHT", new_y="TOP")
        x += width
    pdf.set_y(y0 + row_h)


def build_medications_pdf(
    medications: list[dict[str, Any]],
    *,
    scope: str = "all",
    patient_label: str | None = None,
    patient_subline: str | None = None,
) -> bytes:
    """Landscape medications list PDF with a clean filterable table."""
    scope_key = normalize_medication_export_scope(scope)
    scope_label = medication_export_scope_label(scope_key)
    rows = filter_medications_for_export(medications, scope_key)

    now = datetime.now(timezone.utc)
    exported_at = _format_timestamp(now.isoformat())
    report_date, report_time = _format_timestamp_parts(now.isoformat())

    pdf = AssessmentPDF(
        report_date=report_date,
        report_time=report_time,
        report_type="Medications export",
        exported_at=exported_at,
        patient_label=patient_label,
        patient_subline=patient_subline,
        orientation="L",
    )
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=26)
    pdf.set_margins(12, 36, 12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(14, 116, 144)
    title = "Medications & remedies"
    if patient_label:
        title = f"Medications & remedies - {_safe_text(patient_label)}"
    pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT", align="L")

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(80, 80, 80)
    meta = [f"Scope: {scope_label}", f"Exported: {exported_at}", f"{len(rows)} row{'s' if len(rows) != 1 else ''}"]
    if patient_subline:
        meta.append(_safe_text(patient_subline))
    pdf.cell(0, 4.5, _safe_text(" · ".join(meta)), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(70, 70, 70)
    legend = (
        "Rx = prescription. Non-Rx includes OTC and remedies/supplements. "
        "History lists stopped medications. All includes active and stopped."
    )
    _pdf_multiline(pdf, _safe_text(legend), h=3.6)
    pdf.set_draw_color(14, 165, 233)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3.5)

    usable_w = pdf.w - pdf.l_margin - pdf.r_margin
    include_status = scope_key in {"all", "history"}
    include_ended = scope_key in {"all", "history"}

    # Column plan tuned for landscape A4 (~273mm usable with 12mm margins).
    if include_ended and include_status:
        cols: list[tuple[str, float]] = [
            ("Medication", usable_w * 0.20),
            ("Type", usable_w * 0.07),
            ("Status", usable_w * 0.07),
            ("Dose / frequency", usable_w * 0.18),
            ("Started", usable_w * 0.09),
            ("Ended", usable_w * 0.09),
            ("For", usable_w * 0.14),
            ("Notes", usable_w * 0.16),
        ]
    elif include_ended:
        cols = [
            ("Medication", usable_w * 0.22),
            ("Type", usable_w * 0.08),
            ("Dose / frequency", usable_w * 0.20),
            ("Started", usable_w * 0.10),
            ("Ended", usable_w * 0.10),
            ("For", usable_w * 0.14),
            ("Notes", usable_w * 0.16),
        ]
    else:
        cols = [
            ("Medication", usable_w * 0.24),
            ("Type", usable_w * 0.08),
            ("Dose / frequency", usable_w * 0.22),
            ("Started", usable_w * 0.10),
            ("For", usable_w * 0.16),
            ("Notes", usable_w * 0.20),
        ]

    # Normalize widths to exact usable width (float drift).
    total = sum(w for _, w in cols)
    if total > 0:
        cols = [(label, width * usable_w / total) for label, width in cols]

    if not rows:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 8, "No medications match this export scope.", new_x="LMARGIN", new_y="NEXT")
    else:
        header_h = 7.0
        _write_med_table_header(pdf, cols, row_h=header_h)
        for idx, med in enumerate(rows):
            active = (med.get("status") or "active") != "stopped"
            preferred = _safe_text(str(med.get("name") or "").strip() or "—")
            official = _safe_text(str(med.get("official_name") or "").strip())
            if official and preferred != "—" and official.casefold() != preferred.casefold():
                name_cell = f"{preferred} (official: {official})"
            elif official and preferred == "—":
                name_cell = official
            else:
                name_cell = preferred
            values = [name_cell]
            values.append(_med_category_label(med.get("category")))
            if include_status:
                values.append("Active" if active else "Stopped")
            values.append(_med_dose_line(med))
            values.append(_format_med_export_date(med.get("started_at")))
            if include_ended:
                values.append(_format_med_export_date(med.get("stopped_at")))
            values.append(_med_conditions_line(med))
            values.append((med.get("notes") or "").strip() or "—")

            row_h = _estimate_med_row_height(pdf, list(zip([w for _, w in cols], values)))
            # Leave room for footer
            if pdf.get_y() + row_h > pdf.h - pdf.b_margin - 4:
                pdf.add_page()
                _write_med_table_header(pdf, cols, row_h=header_h)

            _write_med_table_row(
                pdf,
                cols,
                values,
                fill=(idx % 2 == 1),
                row_h=row_h,
            )

        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(
            0,
            4,
            _safe_text(f"End of list · {len(rows)} medication{'s' if len(rows) != 1 else ''}"),
            new_x="LMARGIN",
            new_y="NEXT",
        )

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()


JOURNAL_EXPORT_DAY_OPTIONS = frozenset({1, 2, 3, 4, 7, 10, 20})


def normalize_journal_export_days(raw: str | int | None) -> int | str:
    text = str(raw if raw is not None else "1").strip().lower()
    if text in {"all", "everything"}:
        return "all"
    try:
        days = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("days must be 1, 2, 3, 4, 7, 10, 20, or all") from exc
    if days not in JOURNAL_EXPORT_DAY_OPTIONS:
        raise ValueError("days must be 1, 2, 3, 4, 7, 10, 20, or all")
    return days


def journal_export_days_label(days: int | str) -> str:
    if days == "all":
        return "All entries"
    n = int(days)
    if n == 1:
        return "Today"
    return f"Last {n} days"


def filter_journal_for_export(
    entries: list[dict[str, Any]] | None,
    days: int | str = 1,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Newest-first journal rows for the requested day window (Eastern calendar days)."""
    from datetime import timedelta

    rows = [e for e in (entries or []) if isinstance(e, dict)]
    rows.sort(
        key=lambda e: str(e.get("recorded_at") or e.get("created_at") or ""),
        reverse=True,
    )
    if days == "all":
        return rows
    n = int(days)
    anchor = _to_eastern(now or datetime.now(timezone.utc))
    start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    # Inclusive window: today = 1 day
    start = start - timedelta(days=max(n - 1, 0))
    filtered: list[dict[str, Any]] = []
    for entry in rows:
        raw = entry.get("recorded_at") or entry.get("created_at")
        dt = _parse_iso_datetime(str(raw) if raw else None)
        if not dt:
            continue
        if _to_eastern(dt) >= start:
            filtered.append(entry)
    return filtered


def journal_pdf_filename(
    *,
    patient_label: str | None = None,
    days: int | str = 1,
    exported_at: datetime | None = None,
) -> str:
    stamp = _format_filename_stamp(
        (exported_at or datetime.now(timezone.utc)).isoformat()
    )
    scope_key = "all" if days == "all" else f"{int(days)}d"
    slug = ""
    if patient_label:
        slug = re.sub(r"[^\w\s-]", "", patient_label.lower())
        slug = re.sub(r"[\s_-]+", "-", slug).strip("-")[:36]
    if slug:
        return f"beatit-log-{scope_key}-{slug}-{stamp}.pdf"
    return f"beatit-log-{scope_key}-{stamp}.pdf"


def _format_journal_export_when(iso: str | None) -> str:
    dt = _parse_iso_datetime(iso)
    if not dt:
        return "—"
    eastern = _to_eastern(dt)
    date = eastern.strftime("%b %d, %Y").replace(" 0", " ")
    hour = eastern.strftime("%I").lstrip("0") or "12"
    return f"{date} {hour}:{eastern.strftime('%M %p')}"


def _format_journal_export_time(iso: str | None) -> str:
    dt = _parse_iso_datetime(iso)
    if not dt:
        return "—"
    eastern = _to_eastern(dt)
    hour = eastern.strftime("%I").lstrip("0") or "12"
    return f"{hour}:{eastern.strftime('%M %p')}"


def _format_journal_export_day_heading(iso: str | None) -> str:
    dt = _parse_iso_datetime(iso)
    if not dt:
        return "Unknown day"
    eastern = _to_eastern(dt)
    return eastern.strftime("%A, %b %d, %Y").replace(" 0", " ")


def _group_journal_for_timeline(
    entries: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Newest days first; within each day chronological (oldest→newest), one row each."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for entry in entries:
        raw = entry.get("recorded_at") or entry.get("created_at")
        dt = _parse_iso_datetime(str(raw) if raw else None)
        key = _to_eastern(dt).strftime("%Y-%m-%d") if dt else "unknown"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(entry)
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for key in order:
        day_rows = list(buckets[key])
        day_rows.sort(
            key=lambda e: str(e.get("recorded_at") or e.get("created_at") or "")
        )
        groups.append((key, day_rows))
    return groups


def _write_journal_timeline(pdf: FPDF, rows: list[dict[str, Any]]) -> None:
    """Vertical day-grouped timeline: dedicated row per entry (no overlapping labels)."""
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    time_w = 22.0
    rail_x = pdf.l_margin + time_w + 4.0
    body_x = rail_x + 6.0
    body_w = max(usable - (body_x - pdf.l_margin), 40.0)
    accent = (14, 116, 144)
    muted = (100, 100, 100)
    ink = (30, 30, 30)

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*accent)
    pdf.cell(0, 6, "Timeline", new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*muted)
    pdf.cell(
        0,
        4,
        _safe_text("One row per entry · chronological within each day"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(1.5)

    groups = _group_journal_for_timeline(rows)
    for day_key, day_rows in groups:
        sample = day_rows[0].get("recorded_at") or day_rows[0].get("created_at")
        if pdf.get_y() + 18 > pdf.h - pdf.b_margin:
            pdf.add_page()
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*ink)
        pdf.cell(
            0,
            5.5,
            _safe_text(_format_journal_export_day_heading(str(sample) if sample else None)),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(0.5)

        for idx, entry in enumerate(day_rows):
            label = str(entry.get("label") or "—").strip() or "—"
            kind = str(entry.get("kind") or "note").strip() or "note"
            sev = entry.get("severity")
            sev_text = f"{sev}/5" if sev is not None else ""
            detail = (entry.get("text") or "").strip()
            when = _format_journal_export_time(
                str(entry.get("recorded_at") or entry.get("created_at") or "")
            )
            line1 = f"{kind} · {label}"
            if sev_text:
                line1 = f"{line1} · {sev_text}"
            line1 = _safe_text(line1)
            detail_safe = _safe_text(detail) if detail else ""

            pdf.set_font("Helvetica", "", 8)
            line1_h = 4.2
            detail_h = 0.0
            if detail_safe:
                # Reserve space for up to 2 wrapped detail lines without colliding next row.
                detail_h = min(pdf.get_string_width(detail_safe) / max(body_w, 1) * 3.8 + 3.8, 8.0)
            row_h = max(8.5, line1_h + detail_h + 2.0)

            if pdf.get_y() + row_h > pdf.h - pdf.b_margin - 2:
                pdf.add_page()
                # Repeat day heading after page break
                pdf.set_font("Helvetica", "B", 9)
                pdf.set_text_color(*ink)
                pdf.cell(
                    0,
                    5.5,
                    _safe_text(
                        f"{_format_journal_export_day_heading(str(sample) if sample else None)} (cont.)"
                    ),
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                pdf.ln(0.5)

            y0 = pdf.get_y()
            y_mid = y0 + 3.2
            y1 = y0 + row_h

            # Spine segment — continuous within the day, exclusive per-row body.
            pdf.set_draw_color(*accent)
            pdf.set_line_width(0.45)
            line_top = y0 if idx > 0 else y_mid
            line_bot = y1 if idx < len(day_rows) - 1 else y_mid
            pdf.line(rail_x, line_top, rail_x, line_bot)
            pdf.set_fill_color(*accent)
            pdf.ellipse(rail_x - 1.3, y_mid - 1.3, 2.6, 2.6, style="F")

            pdf.set_xy(pdf.l_margin, y0 + 1.2)
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*muted)
            pdf.cell(time_w, 4, _safe_text(when), align="R")

            pdf.set_xy(body_x, y0 + 1.0)
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_text_color(*ink)
            pdf.cell(body_w, line1_h, line1[:90], new_x="LMARGIN", new_y="NEXT")
            if detail_safe:
                pdf.set_x(body_x)
                pdf.set_font("Helvetica", "", 7.5)
                pdf.set_text_color(*muted)
                pdf.multi_cell(body_w, 3.6, detail_safe[:160], new_x="LMARGIN", new_y="NEXT")

            # Advance past this exclusive row so the next entry cannot overlap.
            pdf.set_y(max(pdf.get_y(), y1))

        pdf.ln(2.5)


def build_journal_pdf(
    entries: list[dict[str, Any]],
    *,
    days: int | str = 1,
    patient_label: str | None = None,
    patient_subline: str | None = None,
) -> bytes:
    """Portrait daily-log PDF for patient self-reports."""
    days_key = normalize_journal_export_days(days)
    rows = filter_journal_for_export(entries, days_key)

    now = datetime.now(timezone.utc)
    exported_at = _format_timestamp(now.isoformat())
    report_date, report_time = _format_timestamp_parts(now.isoformat())

    pdf = AssessmentPDF(
        report_date=report_date,
        report_time=report_time,
        report_type="Log export",
        exported_at=exported_at,
        patient_label=patient_label,
        patient_subline=patient_subline,
        orientation="P",
    )
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=26)
    pdf.set_margins(12, 36, 12)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(14, 116, 144)
    title = "Daily log"
    if patient_label:
        title = f"Daily log - {_safe_text(patient_label)}"
    pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT", align="L")

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(80, 80, 80)
    meta = [
        f"Range: {journal_export_days_label(days_key)}",
        f"Exported: {exported_at}",
        f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}",
    ]
    if patient_subline:
        meta.append(_safe_text(patient_subline))
    pdf.cell(0, 4.5, _safe_text(" · ".join(meta)), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    from app.services.log_observations import build_log_observations

    observations = build_log_observations(entries, days_key)
    if observations:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(14, 116, 144)
        pdf.cell(0, 6, "Observations", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(
            0,
            4,
            _safe_text("Auto insights from this range — not a diagnosis"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(30, 30, 30)
        for obs in observations:
            text = _safe_text(f"- {obs.get('text') or ''}")
            if pdf.get_y() + 8 > pdf.h - pdf.b_margin:
                pdf.add_page()
            pdf.multi_cell(0, 4.5, text, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    usable = pdf.w - pdf.l_margin - pdf.r_margin
    cols: list[tuple[str, float]] = [
        ("When", usable * 0.26),
        ("Kind", usable * 0.12),
        ("Entry", usable * 0.28),
        ("Sev", usable * 0.08),
        ("Details", usable * 0.26),
    ]
    header_h = 7.0

    if not rows:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(80, 80, 80)
        pdf.multi_cell(
            0,
            5,
            _safe_text("No log entries in this range."),
            new_x="LMARGIN",
            new_y="NEXT",
        )
    else:
        _write_journal_timeline(pdf, rows)
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(14, 116, 144)
        pdf.cell(0, 6, "Entry list", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(1)
        _write_med_table_header(pdf, cols, row_h=header_h)
        for idx, entry in enumerate(rows):
            sev = entry.get("severity")
            sev_text = f"{sev}/5" if sev is not None else "—"
            values = [
                _format_journal_export_when(entry.get("recorded_at") or entry.get("created_at")),
                str(entry.get("kind") or "note"),
                str(entry.get("label") or "—").strip() or "—",
                sev_text,
                (entry.get("text") or "").strip() or "—",
            ]
            row_h = _estimate_med_row_height(pdf, list(zip([w for _, w in cols], values)))
            if pdf.get_y() + row_h > pdf.h - pdf.b_margin - 4:
                pdf.add_page()
                _write_med_table_header(pdf, cols, row_h=header_h)
            _write_med_table_row(
                pdf,
                cols,
                values,
                fill=(idx % 2 == 1),
                row_h=row_h,
            )

        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(
            0,
            4,
            _safe_text(
                f"End of list · {len(rows)} entr{'y' if len(rows) == 1 else 'ies'} · {journal_export_days_label(days_key)}"
            ),
            new_x="LMARGIN",
            new_y="NEXT",
        )

    buffer = BytesIO()
    pdf.output(buffer)
    return buffer.getvalue()
