from typing import Any

from app.storage.documents import DocumentStore

# Video files are stored locally; transcription is emulated with a placeholder.
# Replace with Whisper or a remote transcription service when available.


async def ingest_video(
    store: DocumentStore,
    *,
    filename: str,
    content: bytes,
    title: str | None = None,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    meta["original_filename"] = filename
    meta["transcription_status"] = "pending"
    meta["note"] = (
        "Video stored locally. Automatic transcription is not yet connected — "
        "add manual notes or connect Whisper/Ollama vision later."
    )

    placeholder = notes.strip() if notes else (
        f"[Video file stored: {filename}]\n\n"
        "No transcript yet. Add clinical notes or connect a transcription pipeline."
    )

    return await store.create_document(
        title=title or filename,
        source_type="video",
        extracted_text=placeholder,
        raw_filename=filename,
        raw_content=content,
        metadata=meta,
    )
