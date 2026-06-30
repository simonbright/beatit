from io import BytesIO
from typing import Any

from pypdf import PdfReader

from app.storage.documents import DocumentStore


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i} ---\n{text.strip()}")
    return "\n\n".join(pages)


async def ingest_pdf_bytes(
    store: DocumentStore,
    *,
    content: bytes,
    filename: str,
    title: str,
    source_uri: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extracted = extract_pdf_text(content)
    meta = dict(metadata or {})
    meta["original_filename"] = filename
    meta["page_count"] = len(PdfReader(BytesIO(content)).pages)

    return await store.create_document(
        title=title,
        source_type="pdf",
        source_uri=source_uri,
        extracted_text=extracted or "[No extractable text in PDF — may be scanned/image-based]",
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
