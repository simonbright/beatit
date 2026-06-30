from typing import Any

from app.storage.documents import DocumentStore


async def ingest_text(
    store: DocumentStore,
    *,
    title: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await store.create_document(
        title=title,
        source_type="text",
        extracted_text=content.strip(),
        metadata=metadata or {},
    )
