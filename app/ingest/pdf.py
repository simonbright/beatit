"""PDF text extraction with OCR fallback for scanned/image PDFs."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.storage.documents import DocumentStore

MIN_NATIVE_CHARS = 80
EMPTY_PDF_PLACEHOLDER = "[No extractable text in PDF — may be scanned/image-based]"
OCR_PAGE_LIMIT = 20


def _page_count(content: bytes) -> int:
    try:
        return len(PdfReader(BytesIO(content)).pages)
    except Exception:
        return 0


def extract_pdf_text_native(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    pages: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i} ---\n{text.strip()}")
    return "\n\n".join(pages)


def _run_pdftotext(content: bytes) -> str:
    if not shutil.which("pdftotext"):
        return ""
    with tempfile.TemporaryDirectory(prefix="beatit-pdf-") as tmp:
        pdf_path = Path(tmp) / "doc.pdf"
        pdf_path.write_bytes(content)
        out_path = Path(tmp) / "doc.txt"
        try:
            subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf_path), str(out_path)],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        if not out_path.exists():
            return ""
        text = out_path.read_text(encoding="utf-8", errors="ignore").strip()
        return text


def _ocr_with_tesseract(content: bytes, *, max_pages: int = OCR_PAGE_LIMIT) -> str:
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract:
        return ""

    pages_out: list[str] = []
    with tempfile.TemporaryDirectory(prefix="beatit-ocr-") as tmp:
        pdf_path = Path(tmp) / "doc.pdf"
        pdf_path.write_bytes(content)
        prefix = Path(tmp) / "page"
        try:
            subprocess.run(
                [
                    pdftoppm,
                    "-png",
                    "-r",
                    "200",
                    "-f",
                    "1",
                    "-l",
                    str(max_pages),
                    str(pdf_path),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                timeout=300,
            )
        except (subprocess.SubprocessError, OSError):
            return ""

        images = sorted(Path(tmp).glob("page-*.png"))
        for i, image in enumerate(images, start=1):
            try:
                result = subprocess.run(
                    [tesseract, str(image), "stdout", "-l", "eng", "--psm", "6"],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
            except (subprocess.SubprocessError, OSError):
                continue
            text = result.stdout.decode("utf-8", errors="ignore").strip()
            if text:
                pages_out.append(f"--- Page {i} (OCR) ---\n{text}")
    return "\n\n".join(pages_out)


def extract_pdf_text(content: bytes) -> tuple[str, dict[str, Any]]:
    """Return (text, extraction_meta). Uses OCR when native extract is empty/thin."""
    meta: dict[str, Any] = {
        "page_count": _page_count(content),
        "extraction_method": "native",
        "needs_ocr": False,
        "ocr_available": bool(shutil.which("pdftoppm") and shutil.which("tesseract")),
    }

    native = extract_pdf_text_native(content).strip()
    if len(native) >= MIN_NATIVE_CHARS:
        meta["extraction_method"] = "native"
        meta["extracted_chars"] = len(native)
        return native, meta

    pdftotext = _run_pdftotext(content).strip()
    if len(pdftotext) >= MIN_NATIVE_CHARS:
        # Format with page breaks if plain dump
        formatted = pdftotext
        meta["extraction_method"] = "pdftotext"
        meta["extracted_chars"] = len(formatted)
        return formatted, meta

    ocr = _ocr_with_tesseract(content).strip()
    if ocr:
        meta["extraction_method"] = "ocr"
        meta["extracted_chars"] = len(ocr)
        meta["needs_ocr"] = False
        return ocr, meta

    # Keep any thin native text if present; otherwise placeholder
    fallback = native or pdftotext or EMPTY_PDF_PLACEHOLDER
    meta["extraction_method"] = "empty"
    meta["needs_ocr"] = True
    meta["extracted_chars"] = 0 if fallback == EMPTY_PDF_PLACEHOLDER else len(fallback)
    if not meta["ocr_available"]:
        meta["ocr_hint"] = (
            "Install poppler (pdftoppm) and tesseract for automatic OCR of scanned PDFs, "
            "or paste OCR text via Add data / AI Chat."
        )
    return fallback, meta


def is_empty_pdf_extract(text: str | None) -> bool:
    cleaned = (text or "").strip()
    return (not cleaned) or cleaned == EMPTY_PDF_PLACEHOLDER or cleaned.startswith(
        "[No extractable text in PDF"
    )


EMPTY_IMAGE_PLACEHOLDER = "[No extractable text in image — OCR may be unavailable]"
MED_IMPORT_MAX_BYTES = 8 * 1024 * 1024
MED_IMPORT_PDF_TYPES = frozenset({"application/pdf"})
MED_IMPORT_IMAGE_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})
MED_IMPORT_ALLOWED_TYPES = MED_IMPORT_PDF_TYPES | MED_IMPORT_IMAGE_TYPES


def sniff_med_import_kind(
    content: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
) -> str:
    """Return 'pdf' or 'image'. Raises ValueError if unsupported."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    name = (filename or "").lower()
    if ctype in MED_IMPORT_PDF_TYPES or name.endswith(".pdf") or content[:5] == b"%PDF-":
        return "pdf"
    if ctype in MED_IMPORT_IMAGE_TYPES or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "image"
    # Magic bytes for common images
    if content[:3] == b"\xff\xd8\xff":
        return "image"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image"
    raise ValueError("Unsupported file type. Upload a PDF or JPEG/PNG/WebP image.")


def validate_med_import_upload(
    content: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
) -> str:
    if not content:
        raise ValueError("Empty file")
    if len(content) > MED_IMPORT_MAX_BYTES:
        raise ValueError("File must be 8 MB or smaller")
    return sniff_med_import_kind(content, content_type=content_type, filename=filename)


def extract_image_text(content: bytes) -> tuple[str, dict[str, Any]]:
    """OCR a single image (JPEG/PNG/WebP) via tesseract. Returns (text, meta)."""
    tesseract = shutil.which("tesseract")
    meta: dict[str, Any] = {
        "page_count": 1,
        "extraction_method": "ocr",
        "needs_ocr": False,
        "ocr_available": bool(tesseract),
        "source_kind": "image",
    }
    if not tesseract:
        meta["extraction_method"] = "empty"
        meta["needs_ocr"] = True
        meta["extracted_chars"] = 0
        meta["ocr_hint"] = (
            "Install tesseract to OCR medication list photos, "
            "or upload a text-based PDF instead."
        )
        return EMPTY_IMAGE_PLACEHOLDER, meta

    with tempfile.TemporaryDirectory(prefix="beatit-img-ocr-") as tmp:
        img_path = Path(tmp) / "med.png"
        try:
            from PIL import Image

            image = Image.open(BytesIO(content))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(img_path, format="PNG")
        except Exception:
            # Fall back to writing raw bytes if Pillow cannot decode
            raw_path = Path(tmp) / "med.bin"
            raw_path.write_bytes(content)
            img_path = raw_path

        try:
            result = subprocess.run(
                [tesseract, str(img_path), "stdout", "-l", "eng", "--psm", "6"],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.SubprocessError, OSError):
            meta["extraction_method"] = "empty"
            meta["needs_ocr"] = True
            meta["extracted_chars"] = 0
            return EMPTY_IMAGE_PLACEHOLDER, meta

        text = result.stdout.decode("utf-8", errors="ignore").strip()
        if not text:
            meta["extraction_method"] = "empty"
            meta["needs_ocr"] = True
            meta["extracted_chars"] = 0
            return EMPTY_IMAGE_PLACEHOLDER, meta

        meta["extracted_chars"] = len(text)
        return text, meta


def extract_med_list_text(
    content: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Extract text from a medications-list PDF or image upload."""
    kind = validate_med_import_upload(
        content, content_type=content_type, filename=filename
    )
    if kind == "pdf":
        text, meta = extract_pdf_text(content)
        meta = dict(meta)
        meta["source_kind"] = "pdf"
        return text, meta
    return extract_image_text(content)


def is_empty_med_extract(text: str | None) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if is_empty_pdf_extract(cleaned):
        return True
    return cleaned == EMPTY_IMAGE_PLACEHOLDER or cleaned.startswith(
        "[No extractable text in image"
    )


async def ingest_pdf_bytes(
    store: DocumentStore,
    *,
    content: bytes,
    filename: str,
    title: str,
    source_uri: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extracted, extraction_meta = extract_pdf_text(content)
    meta = dict(metadata or {})
    meta["original_filename"] = filename
    meta.update(extraction_meta)

    return await store.create_document(
        title=title,
        source_type="pdf",
        source_uri=source_uri,
        extracted_text=extracted,
        raw_filename=filename,
        raw_content=content,
        metadata=meta,
    )


async def ingest_pdf_file(
    store: DocumentStore,
    *,
    filename: str,
    content: bytes,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await ingest_pdf_bytes(
        store,
        content=content,
        filename=filename,
        title=title or filename,
        metadata=metadata,
    )


async def reextract_pdf_document(store: DocumentStore, doc: dict[str, Any]) -> dict[str, Any]:
    """Re-run text/OCR extraction for an existing PDF document."""
    file_path = doc.get("file_path")
    if not file_path:
        raise ValueError("Document has no stored PDF file")
    path = Path(file_path)
    if not path.exists():
        raise ValueError("Stored PDF file is missing")
    content = path.read_bytes()
    extracted, extraction_meta = extract_pdf_text(content)
    saved = await store.save_extracted_text(doc["id"], extracted)
    meta = dict(doc.get("metadata") or {})
    meta.update(extraction_meta)
    updated = await store.db.update_document_metadata(
        doc["id"],
        metadata=meta,
        extracted_path=str(saved),
    )
    return updated or doc
