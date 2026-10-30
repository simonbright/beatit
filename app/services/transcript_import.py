"""Turn a recorded-visit transcript file into clean, verbatim text.

Supports plain text / Markdown, WebVTT (.vtt), SubRip (.srt), Word (.docx)
and PDF. Timing cues and cue numbers are removed; speaker labels and wording
are kept exactly. Nothing is summarized or rewritten.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from xml.etree import ElementTree

SUPPORTED_EXTENSIONS = (".txt", ".md", ".vtt", ".srt", ".docx", ".pdf")
MAX_VISIT_FILES = 12
VISIT_FILE_MAX_BYTES = 20 * 1024 * 1024
PDF_NO_TEXT_NOTE = (
    "This PDF was saved to the Library. No extractable text was found "
    "(it may be a scan)."
)

_CUE_TIME = re.compile(
    r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->\s*(\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}.*$"
)
_VTT_VOICE = re.compile(r"<v\s+([^>]+)>(.*?)(</v>)?$")
_TAGS = re.compile(r"</?[^>]+>")


def _from_cues(text: str) -> str:
    """Strip VTT/SRT timing, merge consecutive lines from the same speaker."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip("﻿").strip()
        if not line or line.upper().startswith("WEBVTT") or line.startswith(("NOTE", "STYLE", "REGION")):
            continue
        if line.isdigit() or _CUE_TIME.match(line):
            continue
        voice = _VTT_VOICE.match(line)
        if voice:
            line = f"{voice.group(1).strip()}: {_TAGS.sub('', voice.group(2)).strip()}"
        else:
            line = _TAGS.sub("", line).strip()
        if not line:
            continue
        if lines and lines[-1] == line:
            continue  # caption repeats
        lines.append(line)
    merged: list[str] = []
    for line in lines:
        m = re.match(r"^([^:]{1,60}):\s(.*)$", line)
        if merged and m:
            prev = re.match(r"^([^:]{1,60}):\s(.*)$", merged[-1])
            if prev and prev.group(1) == m.group(1):
                merged[-1] = f"{merged[-1]} {m.group(2)}"
                continue
        merged.append(line)
    return "\n".join(merged)


def _from_docx(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("That .docx file could not be read") from exc
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = ElementTree.fromstring(xml)
    paras: list[str] = []
    for p in root.iter(f"{{{ns['w']}}}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{{{ns['w']}}}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{{{ns['w']}}}tab",):
                parts.append("\t")
            elif node.tag in (f"{{{ns['w']}}}br", f"{{{ns['w']}}}cr"):
                parts.append("\n")
        paras.append("".join(parts))
    return "\n".join(paras)


def _from_pdf(data: bytes) -> str:
    from app.ingest.pdf import EMPTY_PDF_PLACEHOLDER, extract_pdf_text_quick

    try:
        body, _meta = extract_pdf_text_quick(data)
    except Exception as exc:  # noqa: BLE001 — surface a visit-friendly error
        raise ValueError("That PDF could not be read") from exc
    body = (body or "").strip()
    if not body or body == EMPTY_PDF_PLACEHOLDER:
        return PDF_NO_TEXT_NOTE
    # Drop the "--- Page N ---" markers so the Library copy stays readable.
    body = re.sub(r"^--- Page \d+ ---\n", "", body, flags=re.MULTILINE)
    return body


def decode_visit_file(file_base64: str) -> bytes:
    try:
        data = base64.b64decode(file_base64 or "", validate=False)
    except ValueError as exc:
        raise ValueError("That file could not be read") from exc
    if not data:
        raise ValueError("That file is empty")
    if len(data) > VISIT_FILE_MAX_BYTES:
        raise ValueError("Each file must be 20 MB or smaller")
    return data


def is_pdf_filename(filename: str | None) -> bool:
    return (filename or "").lower().endswith(".pdf")


def transcript_text(
    *,
    text: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
    max_chars: int = 400_000,
) -> str:
    name = (filename or "").lower()
    if file_base64:
        if name and not name.endswith(SUPPORTED_EXTENSIONS):
            raise ValueError("Use a .txt, .md, .vtt, .srt, .docx or .pdf file, or paste the text")
        data = decode_visit_file(file_base64)
        if name.endswith(".docx"):
            body = _from_docx(data)
        elif name.endswith(".pdf"):
            body = _from_pdf(data)
        else:
            body = data.decode("utf-8", errors="replace")
    else:
        body = text or ""
    if name.endswith((".vtt", ".srt")) or body.lstrip("﻿").startswith("WEBVTT") or _CUE_TIME.search(body[:2000] or "x") or any(
        _CUE_TIME.match(l) for l in body.splitlines()[:20]
    ):
        body = _from_cues(body)
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not body:
        raise ValueError("The transcript is empty")
    if len(body) > max_chars:
        raise ValueError("Transcript is too long to import in one go")
    return body


def speakers(text: str, limit: int = 8) -> list[str]:
    """Speaker labels found at line starts ("Dr. Lee: …"), in order of appearance."""
    out: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Z][^:]{0,40}):\s", line)
        if m:
            label = m.group(1).strip()
            if label not in out and not re.match(r"^\d", label):
                out.append(label)
        if len(out) >= limit:
            break
    return out
