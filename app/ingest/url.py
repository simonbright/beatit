from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.storage.documents import DocumentStore

USER_AGENT = "BeatIt-MedicalResearch/1.0 (+local research tool)"


def _clean_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


async def ingest_url(
    store: DocumentStore,
    *,
    url: str,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    final_url = str(response.url)

    if "pdf" in content_type.lower() or final_url.lower().endswith(".pdf"):
        from app.ingest.pdf import ingest_pdf_bytes

        return await ingest_pdf_bytes(
            store,
            content=response.content,
            filename=_basename_from_url(final_url) or "document.pdf",
            title=title or f"PDF from {urlparse(final_url).netloc}",
            source_uri=final_url,
            metadata=metadata,
        )

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()

    page_title = title or (soup.title.string.strip() if soup.title and soup.title.string else final_url)
    text = _clean_text(soup.get_text(separator="\n"))

    meta = dict(metadata or {})
    meta.update({"content_type": content_type, "final_url": final_url})

    return await store.create_document(
        title=page_title,
        source_type="url",
        source_uri=final_url,
        extracted_text=text,
        metadata=meta,
    )


def _basename_from_url(url: str) -> str:
    path = urlparse(url).path
    return path.rsplit("/", 1)[-1] if path else "document.pdf"
