from pathlib import Path
from typing import Any

from app.config import settings
from app.storage.database import Database


class DocumentStore:
    def __init__(self, db: Database):
        self.db = db

    async def save_extracted_text(self, doc_id: str, text: str) -> Path:
        path = settings.extracted_dir / f"{doc_id}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    async def save_raw_file(self, doc_id: str, filename: str, content: bytes) -> Path:
        safe_name = Path(filename).name
        path = settings.documents_dir / f"{doc_id}_{safe_name}"
        path.write_bytes(content)
        return path

    async def read_extracted_text(self, doc: dict[str, Any]) -> str | None:
        extracted_path = doc.get("extracted_path")
        if not extracted_path:
            return None
        path = Path(extracted_path)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    async def create_document(
        self,
        *,
        title: str,
        source_type: str,
        extracted_text: str | None = None,
        source_uri: str | None = None,
        raw_filename: str | None = None,
        raw_content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        doc = await self.db.insert_document(
            title=title,
            source_type=source_type,
            source_uri=source_uri,
            metadata=metadata,
        )
        doc_id = doc["id"]

        file_path = None
        extracted_path = None

        if raw_content and raw_filename:
            saved = await self.save_raw_file(doc_id, raw_filename, raw_content)
            file_path = str(saved)

        if extracted_text:
            saved_text = await self.save_extracted_text(doc_id, extracted_text)
            extracted_path = str(saved_text)

        if file_path or extracted_path:
            await self.db.update_document_paths(
                doc_id,
                file_path=file_path,
                extracted_path=extracted_path,
            )

        updated = await self.db.get_document(doc_id)
        return updated or doc

    async def delete_document(self, doc_id: str) -> bool:
        doc = await self.db.get_document(doc_id)
        if not doc:
            return False

        for key in ("file_path", "extracted_path"):
            path_str = doc.get(key)
            if path_str:
                path = Path(path_str)
                if path.exists():
                    path.unlink()

        return await self.db.delete_document(doc_id)

    async def get_corpus(self, document_ids: list[str] | None = None) -> list[dict[str, Any]]:
        docs = await self.db.list_documents()
        if document_ids:
            id_set = set(document_ids)
            docs = [d for d in docs if d["id"] in id_set]

        corpus = []
        for doc in docs:
            text = await self.read_extracted_text(doc)
            if text:
                corpus.append(
                    {
                        "id": doc["id"],
                        "title": doc["title"],
                        "source_type": doc["source_type"],
                        "source_uri": doc.get("source_uri"),
                        "text": text,
                    }
                )
        return corpus
