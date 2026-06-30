import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import aiosqlite

from app.services.openrouter_models import DEFAULT_OPENROUTER_MODEL

from app.config import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or settings.db_path)

    async def init(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.documents_dir.mkdir(parents=True, exist_ok=True)
        settings.extracted_dir.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_uri TEXT,
                    file_path TEXT,
                    extracted_path TEXT,
                    metadata_json TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    response TEXT NOT NULL,
                    document_ids_json TEXT DEFAULT '[]',
                    model TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

            default_model = settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
            existing = await self.get_setting("openrouter_model")
            if not existing:
                await self.set_setting("openrouter_model", default_model)

    async def get_setting(self, key: str) -> str | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            await db.commit()

    async def insert_document(
        self,
        *,
        title: str,
        source_type: str,
        source_uri: str | None = None,
        file_path: str | None = None,
        extracted_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        doc_id = str(uuid4())
        now = _now_iso()
        row = {
            "id": doc_id,
            "title": title,
            "source_type": source_type,
            "source_uri": source_uri,
            "file_path": file_path,
            "extracted_path": extracted_path,
            "metadata_json": json.dumps(metadata or {}),
            "created_at": now,
            "updated_at": now,
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO documents
                (id, title, source_type, source_uri, file_path, extracted_path, metadata_json, created_at, updated_at)
                VALUES (:id, :title, :source_type, :source_uri, :file_path, :extracted_path, :metadata_json, :created_at, :updated_at)
                """,
                row,
            )
            await db.commit()
        return self._row_to_doc(row)

    async def list_documents(self) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        return [self._row_to_doc(dict(row)) for row in rows]

    async def get_document(self, doc_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            )
            row = await cursor.fetchone()
        return self._row_to_doc(dict(row)) if row else None

    async def update_document_paths(
        self,
        doc_id: str,
        *,
        file_path: str | None = None,
        extracted_path: str | None = None,
    ) -> None:
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE documents
                SET file_path = COALESCE(?, file_path),
                    extracted_path = COALESCE(?, extracted_path),
                    updated_at = ?
                WHERE id = ?
                """,
                (file_path, extracted_path, now, doc_id),
            )
            await db.commit()

    async def delete_document(self, doc_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM documents WHERE id = ?", (doc_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def insert_analysis(
        self,
        *,
        query: str,
        response: str,
        document_ids: list[str],
        model: str | None,
    ) -> dict[str, Any]:
        analysis_id = str(uuid4())
        now = _now_iso()
        row = {
            "id": analysis_id,
            "query": query,
            "response": response,
            "document_ids_json": json.dumps(document_ids),
            "model": model,
            "created_at": now,
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO analyses (id, query, response, document_ids_json, model, created_at)
                VALUES (:id, :query, :response, :document_ids_json, :model, :created_at)
                """,
                row,
            )
            await db.commit()
        return {
            **row,
            "document_ids": document_ids,
        }

    async def list_analyses(self, limit: int = 50) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [self._analysis_row(dict(row)) for row in rows]

    @staticmethod
    def _row_to_doc(row: dict[str, Any]) -> dict[str, Any]:
        metadata = json.loads(row.get("metadata_json") or "{}")
        return {
            "id": row["id"],
            "title": row["title"],
            "source_type": row["source_type"],
            "source_uri": row.get("source_uri"),
            "file_path": row.get("file_path"),
            "extracted_path": row.get("extracted_path"),
            "metadata": metadata,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _analysis_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "query": row["query"],
            "response": row["response"],
            "document_ids": json.loads(row.get("document_ids_json") or "[]"),
            "model": row.get("model"),
            "created_at": row["created_at"],
        }
