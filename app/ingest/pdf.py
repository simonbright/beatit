"""PDF text extraction with OCR fallback for scanned/image PDFs."""

from __future__ import annotations

import base64
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
OCR_VISION_PAGE_LIMIT = 8
OCR_RENDER_DPI = 200

OCR_VISION_SYSTEM = (
    "You are a precise OCR engine for clinical documents. "
    "Transcribe all readable text exactly. Preserve headings, labels, numbers, "
    "tables, and line breaks as plain text. Do not summarize, diagnose, or omit values."
)


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


def _pymupdf_available() -> bool:
    try:
        import pymupdf  # noqa: F401

        return True
    except Exception:
        try:
            import fitz  # noqa: F401

            return True
        except Exception:
            return False


def _tesseract_available() -> bool:
    return bool(shutil.which("tesseract"))


def _pdftoppm_available() -> bool:
    return bool(shutil.which("pdftoppm"))


def ocr_runtime_status() -> dict[str, Any]:
    return {
        "tesseract": _tesseract_available(),
        "pdftoppm": _pdftoppm_available(),
        "pymupdf": _pymupdf_available(),
        "local_ocr": _tesseract_available()
        and (_pdftoppm_available() or _pymupdf_available()),
    }


def _render_pdf_pages_pdftoppm(
    content: bytes, *, max_pages: int, dpi: int
) -> list[bytes]:
    if not _pdftoppm_available():
        return []
    with tempfile.TemporaryDirectory(prefix="beatit-ocr-") as tmp:
        pdf_path = Path(tmp) / "doc.pdf"
        pdf_path.write_bytes(content)
        prefix = Path(tmp) / "page"
        try:
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    str(dpi),
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
            return []
        return [p.read_bytes() for p in sorted(Path(tmp).glob("page-*.png"))]


def _render_pdf_pages_pymupdf(
    content: bytes, *, max_pages: int, dpi: int
) -> list[bytes]:
    try:
        try:
            import pymupdf as fitz
        except Exception:
            import fitz
    except Exception:
        return []
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        return []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    pages: list[bytes] = []
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(pix.tobytes("png"))
    finally:
        doc.close()
    return pages


def render_pdf_page_pngs(
    content: bytes,
    *,
    max_pages: int = OCR_PAGE_LIMIT,
    dpi: int = OCR_RENDER_DPI,
) -> tuple[list[bytes], str]:
    """Return (png_pages, renderer_name). Prefers pdftoppm, falls back to PyMuPDF."""
    pages = _render_pdf_pages_pdftoppm(content, max_pages=max_pages, dpi=dpi)
    if pages:
        return pages, "pdftoppm"
    pages = _render_pdf_pages_pymupdf(content, max_pages=max_pages, dpi=dpi)
    if pages:
        return pages, "pymupdf"
    return [], "none"


def _tesseract_png_bytes(png: bytes) -> str:
    tesseract = shutil.which("tesseract")
    if not tesseract or not png:
        return ""
    with tempfile.TemporaryDirectory(prefix="beatit-tess-") as tmp:
        img_path = Path(tmp) / "page.png"
        img_path.write_bytes(png)
        try:
            result = subprocess.run(
                [tesseract, str(img_path), "stdout", "-l", "eng", "--psm", "6"],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.SubprocessError, OSError):
            return ""
        return result.stdout.decode("utf-8", errors="ignore").strip()


def _ocr_with_tesseract(content: bytes, *, max_pages: int = OCR_PAGE_LIMIT) -> str:
    if not _tesseract_available():
        return ""
    pages, _renderer = render_pdf_page_pngs(content, max_pages=max_pages)
    if not pages:
        return ""
    pages_out: list[str] = []
    for i, png in enumerate(pages, start=1):
        text = _tesseract_png_bytes(png)
        if text:
            pages_out.append(f"--- Page {i} (OCR) ---\n{text}")
    return "\n\n".join(pages_out)


def _png_to_jpeg_bytes(png: bytes, *, max_side: int = 1600, quality: int = 75) -> bytes:
    """Downscale/compress page images so vision OCR requests stay small and fast."""
    try:
        from PIL import Image

        image = Image.open(BytesIO(png))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")
        w, h = image.size
        scale = min(1.0, float(max_side) / float(max(w, h) or 1))
        if scale < 1.0:
            image = image.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        out = BytesIO()
        image.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:
        return png


def _vision_ocr_model() -> str:
    from app.config import settings
    from app.services.openrouter_models import DEFAULT_OPENROUTER_MODEL

    model = (settings.openrouter_model or DEFAULT_OPENROUTER_MODEL).strip()
    lower = model.lower()
    # Text-only Llama instruct models cannot OCR page images
    if "llama" in lower and "vision" not in lower:
        return DEFAULT_OPENROUTER_MODEL
    if any(
        token in lower
        for token in ("gemini", "gpt-4o", "gpt-5", "claude", "vision", "flash")
    ):
        return model
    return DEFAULT_OPENROUTER_MODEL


async def _ocr_pdf_direct_with_vision(content: bytes) -> tuple[str, dict[str, Any]]:
    """Send the PDF bytes straight to a multimodal model (best path for Gemini)."""
    from app.config import settings
    from app.services.openrouter_client import OpenRouterClient

    meta: dict[str, Any] = {"vision_mode": "pdf_direct"}
    if not settings.openrouter_api_key:
        meta["vision_error"] = "OPENROUTER_API_KEY not set"
        return "", meta
    if not content.startswith(b"%PDF"):
        meta["vision_error"] = "Not a PDF"
        return "", meta
    # Keep payload reasonable for API gateways
    if len(content) > 12 * 1024 * 1024:
        meta["vision_error"] = "PDF too large for direct vision OCR"
        return "", meta

    model = _vision_ocr_model()
    client = OpenRouterClient(model=model)
    meta["vision_model"] = model
    b64 = base64.b64encode(content).decode("ascii")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": OCR_VISION_SYSTEM},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "This attachment is a clinical PDF (often a scan). "
                        "Transcribe ALL readable text exactly. Preserve headings, "
                        "labels, numbers, and line breaks. Return only the transcript."
                    ),
                },
                {
                    "type": "file",
                    "file": {
                        "filename": "clinical-report.pdf",
                        "file_data": f"data:application/pdf;base64,{b64}",
                    },
                },
            ],
        },
    ]
    try:
        text = (await client.chat(messages=messages, temperature=0.0)).strip()
    except Exception as exc:
        # Some providers reject the file part — retry as image_url data-PDF
        try:
            messages[1]["content"][1] = {
                "type": "image_url",
                "image_url": {"url": f"data:application/pdf;base64,{b64}"},
            }
            text = (await client.chat(messages=messages, temperature=0.0)).strip()
            meta["vision_mode"] = "pdf_as_image_url"
        except Exception as exc2:
            meta["vision_error"] = f"{exc}; retry: {exc2}"
            return "", meta
    if text:
        return f"--- Page 1 (Vision OCR) ---\n{text}", meta
    meta["vision_error"] = "Vision model returned empty transcript"
    return "", meta


async def _ocr_pages_with_vision(
    content: bytes, *, max_pages: int = OCR_VISION_PAGE_LIMIT
) -> tuple[str, dict[str, Any]]:
    """Render PDF pages and OCR via OpenRouter vision."""
    from app.config import settings
    from app.services.openrouter_client import OpenRouterClient

    meta: dict[str, Any] = {"vision_mode": "page_images"}
    if not settings.openrouter_api_key:
        meta["vision_error"] = "OPENROUTER_API_KEY not set"
        return "", meta

    pages, renderer = render_pdf_page_pngs(
        content, max_pages=max_pages, dpi=150
    )
    meta["renderer"] = renderer
    if not pages:
        meta["vision_error"] = "Could not render PDF pages for vision OCR"
        return "", meta

    model = _vision_ocr_model()
    client = OpenRouterClient(model=model)
    meta["vision_model"] = model
    pages_out: list[str] = []
    for i, png in enumerate(pages, start=1):
        jpeg = _png_to_jpeg_bytes(png)
        b64 = base64.b64encode(jpeg).decode("ascii")
        mime = "image/jpeg" if jpeg[:3] == b"\xff\xd8\xff" else "image/png"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": OCR_VISION_SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Transcribe page {i} of {len(pages)} from this clinical PDF. "
                            "Return only the transcribed text."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ]
        try:
            text = (await client.chat(messages=messages, temperature=0.0)).strip()
        except Exception as exc:
            meta["vision_error"] = str(exc)
            break
        if text:
            pages_out.append(f"--- Page {i} (Vision OCR) ---\n{text}")
    return "\n\n".join(pages_out), meta


async def _ocr_with_openrouter_vision(
    content: bytes, *, max_pages: int = OCR_VISION_PAGE_LIMIT
) -> tuple[str, dict[str, Any]]:
    """OCR scanned PDF via OpenRouter: prefer direct PDF, then rendered pages."""
    text, meta = await _ocr_pdf_direct_with_vision(content)
    if text and len(text.strip()) >= MIN_NATIVE_CHARS:
        meta["vision_attempted"] = True
        return text, meta

    page_text, page_meta = await _ocr_pages_with_vision(content, max_pages=max_pages)
    combined = dict(meta)
    combined["vision_attempted"] = True
    combined["pdf_direct"] = {
        "ok": bool(text),
        "chars": len(text or ""),
        "error": meta.get("vision_error"),
        "mode": meta.get("vision_mode"),
    }
    combined.update(page_meta)
    if page_text and len(page_text.strip()) >= MIN_NATIVE_CHARS:
        return page_text, combined
    if page_text.strip():
        return page_text, combined
    if text.strip():
        return text, combined
    if not combined.get("vision_error"):
        combined["vision_error"] = meta.get("vision_error") or page_meta.get(
            "vision_error"
        ) or "Vision OCR returned no text"
    return "", combined


def extract_pdf_text(content: bytes) -> tuple[str, dict[str, Any]]:
    """Return (text, extraction_meta). Uses OCR when native extract is empty/thin."""
    runtime = ocr_runtime_status()
    meta: dict[str, Any] = {
        "page_count": _page_count(content),
        "extraction_method": "native",
        "needs_ocr": False,
        "ocr_available": runtime["local_ocr"],
        "ocr_runtime": runtime,
    }

    native = extract_pdf_text_native(content).strip()
    if len(native) >= MIN_NATIVE_CHARS:
        meta["extraction_method"] = "native"
        meta["extracted_chars"] = len(native)
        return native, meta

    pdftotext = _run_pdftotext(content).strip()
    if len(pdftotext) >= MIN_NATIVE_CHARS:
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
            "Local OCR tools are unavailable on this server. "
            "Re-extract will try OpenRouter vision OCR automatically."
        )
    return fallback, meta


async def extract_pdf_text_async(content: bytes) -> tuple[str, dict[str, Any]]:
    """Like extract_pdf_text, then OpenRouter vision OCR if local OCR failed."""
    text, meta = extract_pdf_text(content)
    if not meta.get("needs_ocr"):
        return text, meta
    if len((text or "").strip()) >= MIN_NATIVE_CHARS and text != EMPTY_PDF_PLACEHOLDER:
        return text, meta

    vision_text, vision_meta = await _ocr_with_openrouter_vision(content)
    meta.update(vision_meta)
    if vision_text and len(vision_text.strip()) >= MIN_NATIVE_CHARS:
        meta["extraction_method"] = "vision_ocr"
        meta["extracted_chars"] = len(vision_text)
        meta["needs_ocr"] = False
        meta.pop("ocr_hint", None)
        return vision_text, meta

    if vision_text.strip():
        meta["extraction_method"] = "vision_ocr_thin"
        meta["extracted_chars"] = len(vision_text)
        meta["needs_ocr"] = len(vision_text) < MIN_NATIVE_CHARS
        return vision_text, meta

    err = vision_meta.get("vision_error") or meta.get("vision_error")
    if err:
        meta["ocr_hint"] = f"OCR failed: {err}"
    return text, meta


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
    from app.services.clinical_report_classify import classify_and_update_document

    extracted, extraction_meta = await extract_pdf_text_async(content)
    meta = dict(metadata or {})
    meta["original_filename"] = filename
    meta.update(extraction_meta)

    doc = await store.create_document(
        title=title,
        source_type="pdf",
        source_uri=source_uri,
        extracted_text=extracted,
        raw_filename=filename,
        raw_content=content,
        metadata=meta,
    )
    try:
        doc = await classify_and_update_document(
            store, doc, extracted_text=extracted
        )
    except Exception:
        # Classification is best-effort; never fail ingest.
        pass
    return doc


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
    from app.services.clinical_report_classify import classify_and_update_document
    from app.services.document_paths import heal_document_paths, resolve_document_file_path

    doc = await heal_document_paths(store, doc)
    prefer = [store.documents_dir] if hasattr(store, "documents_dir") else None
    path = resolve_document_file_path(doc, prefer_dirs=prefer)
    if not path:
        raise ValueError(
            "Stored PDF file is missing on disk. Re-upload the PDF with Replace file, then try Re-extract again."
        )
    content = path.read_bytes()
    extracted, extraction_meta = await extract_pdf_text_async(content)
    saved = await store.save_extracted_text(doc["id"], extracted)
    meta = dict(doc.get("metadata") or {})
    meta.update(extraction_meta)
    updated = await store.db.update_document_metadata(
        doc["id"],
        metadata=meta,
        extracted_path=str(saved),
    )
    updated = updated or {**doc, "metadata": meta, "extracted_path": str(saved)}
    try:
        updated = await classify_and_update_document(
            store, updated, extracted_text=extracted
        )
    except Exception:
        pass
    return updated
