import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import aiosqlite

from app.services.openrouter_models import (
    DEFAULT_OPENROUTER_MODEL,
    DEPRECATED_OPENROUTER_MODELS,
)
from app.services.patient_context import DEFAULT_PATIENT_CONTEXT
from app.services.assessment_parse import parse_assessment
from app.services.source_references import build_reference_bundle
from app.services.source_normalize import enrich_with_sources
from app.services.content_policy import filter_palliative_content

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

            if not await self.get_setting("patient_context"):
                await self.set_setting("patient_context", DEFAULT_PATIENT_CONTEXT)

            await self._migrate_analyses_columns(db)
            await self._migrate_open_items_table(db)
            await self._migrate_analysis_jobs_table(db)

        await self._migrate_stale_openrouter_model()

    async def _migrate_stale_openrouter_model(self) -> None:
        current = await self.get_setting("openrouter_model")
        if current and current in DEPRECATED_OPENROUTER_MODELS:
            await self.set_setting("openrouter_model", DEFAULT_OPENROUTER_MODEL)

    async def _migrate_open_items_table(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS open_items (
                id TEXT PRIMARY KEY,
                analysis_id TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 1,
                item TEXT NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'Item',
                status TEXT NOT NULL DEFAULT 'open',
                investigation_response TEXT,
                investigation_at TEXT,
                investigation_model TEXT,
                comments_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_open_items_analysis ON open_items(analysis_id)"
        )
        await db.commit()
        await self._migrate_open_items_comments(db)
        await self._migrate_open_items_investigation_draft(db)
        await self._backfill_open_items_from_json(db)

    async def _migrate_open_items_investigation_draft(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(open_items)")
        columns = {row[1] for row in await cursor.fetchall()}
        additions = {
            "investigation_guidance": "TEXT",
            "investigation_draft_response": "TEXT",
            "investigation_draft_at": "TEXT",
            "investigation_draft_model": "TEXT",
        }
        for column, col_type in additions.items():
            if column not in columns:
                await db.execute(
                    f"ALTER TABLE open_items ADD COLUMN {column} {col_type}"
                )
        await db.commit()

    async def _migrate_open_items_comments(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(open_items)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "comments_json" not in columns:
            await db.execute(
                "ALTER TABLE open_items ADD COLUMN comments_json TEXT DEFAULT '[]'"
            )
            await db.commit()

    async def _backfill_open_items_from_json(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("SELECT COUNT(*) FROM open_items")
        count = (await cursor.fetchone())[0]
        if count > 0:
            return

        cursor = await db.execute(
            "SELECT id, open_items_json FROM analyses ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        for analysis_id, open_items_json in rows:
            items = json.loads(open_items_json or "[]")
            if items:
                await self._insert_open_items(db, analysis_id, items)
        await db.commit()

    async def _insert_open_items(
        self,
        db: aiosqlite.Connection,
        analysis_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        now = _now_iso()
        for raw in items:
            item_id = raw.get("id") or str(uuid4())
            priority = int(str(raw.get("priority") or "1").split(".")[0] or 1)
            status = (raw.get("status") or "open").lower()
            if status == "open":
                status = "open"
            await db.execute(
                """
                INSERT INTO open_items
                (id, analysis_id, priority, item, item_type, status,
                 investigation_response, investigation_at, investigation_model,
                 comments_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    analysis_id,
                    priority,
                    raw.get("item") or "",
                    raw.get("type") or raw.get("item_type") or "Item",
                    status,
                    raw.get("investigation_response"),
                    raw.get("investigation_at"),
                    raw.get("investigation_model"),
                    json.dumps(raw.get("comments") or []),
                    now,
                    now,
                ),
            )

    async def sync_open_items_for_analysis(
        self,
        analysis_id: str,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM open_items WHERE analysis_id = ?", (analysis_id,)
            )
            await self._insert_open_items(db, analysis_id, items)
            await db.commit()
        return await self.list_open_items_for_analysis(analysis_id)

    async def list_open_items_for_analysis(
        self, analysis_id: str
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM open_items
                WHERE analysis_id = ?
                ORDER BY priority ASC, created_at ASC
                """,
                (analysis_id,),
            )
            rows = await cursor.fetchall()
        return [self._open_item_row(dict(row)) for row in rows]

    async def get_open_item(self, open_item_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM open_items WHERE id = ?", (open_item_id,)
            )
            row = await cursor.fetchone()
        return self._open_item_row(dict(row)) if row else None

    async def get_analysis_by_id(self, analysis_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return await self._enrich_analysis_row(dict(row))

    async def update_open_item(
        self,
        open_item_id: str,
        *,
        status: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any] | None:
        item = await self.get_open_item(open_item_id)
        if not item:
            return None

        comments = list(item.get("comments") or [])
        if comment and comment.strip():
            comments.append(
                {
                    "id": str(uuid4()),
                    "text": comment.strip(),
                    "created_at": _now_iso(),
                }
            )

        if status is None and not comment:
            return item

        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            if status is not None:
                await db.execute(
                    """
                    UPDATE open_items
                    SET status = ?, comments_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, json.dumps(comments), now, open_item_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE open_items
                    SET comments_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(comments), now, open_item_id),
                )
            await db.commit()
        return await self.get_open_item(open_item_id)

    async def update_open_item_status(
        self, open_item_id: str, status: str
    ) -> dict[str, Any] | None:
        return await self.update_open_item(open_item_id, status=status)

    async def save_open_item_investigation_draft(
        self,
        open_item_id: str,
        *,
        response: str,
        model: str | None,
        guidance: str | None = None,
    ) -> dict[str, Any] | None:
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE open_items
                SET status = 'pending_review',
                    investigation_draft_response = ?,
                    investigation_draft_at = ?,
                    investigation_draft_model = ?,
                    investigation_guidance = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (response, now, model, guidance, now, open_item_id),
            )
            await db.commit()
        return await self.get_open_item(open_item_id)

    async def accept_open_item_investigation(
        self,
        open_item_id: str,
        *,
        response: str | None = None,
    ) -> dict[str, Any] | None:
        item = await self.get_open_item(open_item_id)
        if not item:
            return None
        final = (response or item.get("investigation_draft_response") or "").strip()
        if not final:
            return item
        now = _now_iso()
        model = item.get("investigation_draft_model") or item.get("investigation_model")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE open_items
                SET status = 'investigated',
                    investigation_response = ?,
                    investigation_at = ?,
                    investigation_model = ?,
                    investigation_draft_response = NULL,
                    investigation_draft_at = NULL,
                    investigation_draft_model = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (final, now, model, now, open_item_id),
            )
            await db.commit()
        return await self.get_open_item(open_item_id)

    async def discard_open_item_investigation_draft(
        self, open_item_id: str
    ) -> dict[str, Any] | None:
        item = await self.get_open_item(open_item_id)
        if not item:
            return None
        now = _now_iso()
        next_status = "investigated" if item.get("investigation_response") else "open"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE open_items
                SET status = ?,
                    investigation_draft_response = NULL,
                    investigation_draft_at = NULL,
                    investigation_draft_model = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (next_status, now, open_item_id),
            )
            await db.commit()
        return await self.get_open_item(open_item_id)

    async def add_open_item_investigation_draft_as_comment(
        self, open_item_id: str
    ) -> dict[str, Any] | None:
        item = await self.get_open_item(open_item_id)
        if not item:
            return None
        draft = (item.get("investigation_draft_response") or "").strip()
        if not draft:
            return item
        header = "Investigation draft"
        if item.get("investigation_guidance"):
            header = f"Investigation draft — guidance: {item['investigation_guidance'][:120]}"
        await self.update_open_item(
            open_item_id,
            comment=f"{header}\n\n{draft}",
        )
        next_status = "investigated" if item.get("investigation_response") else "open"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE open_items
                SET status = ?,
                    investigation_draft_response = NULL,
                    investigation_draft_at = NULL,
                    investigation_draft_model = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (next_status, _now_iso(), open_item_id),
            )
            await db.commit()
        return await self.get_open_item(open_item_id)

    async def save_open_item_investigation(
        self,
        open_item_id: str,
        *,
        response: str,
        model: str | None,
    ) -> dict[str, Any] | None:
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE open_items
                SET status = 'investigated',
                    investigation_response = ?,
                    investigation_at = ?,
                    investigation_model = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (response, now, model, now, open_item_id),
            )
            await db.commit()
        return await self.get_open_item(open_item_id)

    @staticmethod
    def _open_item_row(row: dict[str, Any]) -> dict[str, Any]:
        investigation = row.get("investigation_response")
        if investigation:
            investigation = filter_palliative_content(investigation) or None
        draft = row.get("investigation_draft_response")
        if draft:
            draft = filter_palliative_content(draft) or None
        return {
            "id": row["id"],
            "analysis_id": row["analysis_id"],
            "priority": row["priority"],
            "item": row["item"],
            "type": row["item_type"],
            "item_type": row["item_type"],
            "status": row["status"],
            "investigation_response": investigation,
            "investigation_at": row.get("investigation_at"),
            "investigation_model": row.get("investigation_model"),
            "investigation_guidance": row.get("investigation_guidance"),
            "investigation_draft_response": draft,
            "investigation_draft_at": row.get("investigation_draft_at"),
            "investigation_draft_model": row.get("investigation_draft_model"),
            "comments": json.loads(row.get("comments_json") or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def _migrate_analysis_jobs_table(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                job_type TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                document_ids_json TEXT DEFAULT '[]',
                include_baseline_assessment INTEGER DEFAULT 0,
                analysis_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (analysis_id) REFERENCES analyses(id)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_jobs_status ON analysis_jobs(status)"
        )
        await db.commit()

    async def fail_stale_analysis_jobs(self, reason: str = "Interrupted by server restart") -> None:
        now = _now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE analysis_jobs
                SET status = 'failed',
                    error = ?,
                    completed_at = ?
                WHERE status IN ('pending', 'running')
                """,
                (reason, now),
            )
            await db.commit()

    async def create_analysis_job(
        self,
        *,
        job_type: str,
        query: str = "",
        document_ids: list[str] | None = None,
        include_baseline_assessment: bool = False,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        now = _now_iso()
        row = {
            "id": job_id,
            "status": "pending",
            "job_type": job_type,
            "query": query,
            "document_ids_json": json.dumps(document_ids or []),
            "include_baseline_assessment": 1 if include_baseline_assessment else 0,
            "analysis_id": None,
            "error": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO analysis_jobs
                (id, status, job_type, query, document_ids_json,
                 include_baseline_assessment, analysis_id, error,
                 created_at, started_at, completed_at)
                VALUES (:id, :status, :job_type, :query, :document_ids_json,
                        :include_baseline_assessment, :analysis_id, :error,
                        :created_at, :started_at, :completed_at)
                """,
                row,
            )
            await db.commit()
        return self._analysis_job_row(row)

    async def get_active_analysis_job(self) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM analysis_jobs
                WHERE status IN ('pending', 'running')
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
        return self._analysis_job_row(dict(row)) if row else None

    async def get_analysis_job(self, job_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)
            )
            row = await cursor.fetchone()
        return self._analysis_job_row(dict(row)) if row else None

    async def update_analysis_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        analysis_id: str | None = None,
        error: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if analysis_id is not None:
            fields.append("analysis_id = ?")
            values.append(analysis_id)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if started_at is not None:
            fields.append("started_at = ?")
            values.append(started_at)
        if completed_at is not None:
            fields.append("completed_at = ?")
            values.append(completed_at)
        if not fields:
            return
        values.append(job_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE analysis_jobs SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            await db.commit()

    @staticmethod
    def _analysis_job_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "status": row["status"],
            "job_type": row["job_type"],
            "query": row.get("query") or "",
            "document_ids": json.loads(row.get("document_ids_json") or "[]"),
            "include_baseline_assessment": bool(row.get("include_baseline_assessment")),
            "analysis_id": row.get("analysis_id"),
            "error": row.get("error"),
            "created_at": row["created_at"],
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
        }

    async def _migrate_analyses_columns(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(analyses)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "analysis_type" not in columns:
            await db.execute(
                "ALTER TABLE analyses ADD COLUMN analysis_type TEXT DEFAULT 'query'"
            )
            await db.commit()
        if "executive_summary" not in columns:
            await db.execute("ALTER TABLE analyses ADD COLUMN executive_summary TEXT")
            await db.commit()
        if "open_items_json" not in columns:
            await db.execute("ALTER TABLE analyses ADD COLUMN open_items_json TEXT DEFAULT '[]'")
            await db.commit()

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
        analysis_type: str = "query",
        executive_summary: str | None = None,
        open_items_json: str | None = None,
    ) -> dict[str, Any]:
        analysis_id = str(uuid4())
        now = _now_iso()
        row = {
            "id": analysis_id,
            "query": query,
            "response": response,
            "document_ids_json": json.dumps(document_ids),
            "model": model,
            "analysis_type": analysis_type,
            "executive_summary": executive_summary,
            "open_items_json": open_items_json or "[]",
            "created_at": now,
        }
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO analyses
                (id, query, response, document_ids_json, model, analysis_type,
                 executive_summary, open_items_json, created_at)
                VALUES (:id, :query, :response, :document_ids_json, :model, :analysis_type,
                        :executive_summary, :open_items_json, :created_at)
                """,
                row,
            )
            await db.commit()
        parsed_items = json.loads(row["open_items_json"])
        open_items = await self.sync_open_items_for_analysis(analysis_id, parsed_items)
        enriched = await self.get_analysis_by_id(analysis_id)
        return enriched or {
            **row,
            "document_ids": document_ids,
            "open_items": open_items,
        }

    async def get_latest_analysis(self) -> dict[str, Any] | None:
        item = await self.get_analysis_by_id_from_latest()
        return item

    async def get_analysis_by_id_from_latest(self) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM analyses ORDER BY created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return await self._enrich_analysis_row(dict(row))

    async def list_analyses(self, limit: int = 50) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM analyses ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        enriched: list[dict[str, Any]] = []
        for row in rows:
            enriched.append(await self._enrich_analysis_row(dict(row)))
        return enriched

    async def _document_titles_for_ids(self, doc_ids: list[str]) -> list[str]:
        titles: list[str] = []
        for doc_id in doc_ids:
            doc = await self.get_document(doc_id)
            if doc and doc.get("title"):
                titles.append(doc["title"])
        return titles

    async def _enrich_analysis_row(self, row: dict[str, Any]) -> dict[str, Any]:
        analysis = self._analysis_row(row)
        db_items = await self.list_open_items_for_analysis(analysis["id"])
        if db_items:
            analysis["open_items"] = db_items
        elif analysis["open_items"]:
            analysis["open_items"] = await self.sync_open_items_for_analysis(
                analysis["id"], analysis["open_items"]
            )

        titles = await self._document_titles_for_ids(analysis["document_ids"])
        response, level = enrich_with_sources(
            filter_palliative_content(analysis["response"]), titles, annotate_staging=True
        )
        summary, _ = enrich_with_sources(
            filter_palliative_content(analysis["executive_summary"]),
            titles,
            annotate_staging=False,
        )
        analysis["response"] = response
        analysis["executive_summary"] = summary
        analysis["source_attribution"] = level
        analysis["document_titles"] = titles

        ref_bundle = build_reference_bundle(
            executive_summary=summary,
            response=response,
        )
        analysis["references"] = ref_bundle["appendix"]
        analysis["executive_summary_display"] = ref_bundle["sections"]["executive_summary"]["body"]
        analysis["executive_summary_refs"] = ref_bundle["sections"]["executive_summary"]["references"]
        analysis["response_display"] = ref_bundle["sections"]["response"]["body"]
        analysis["response_refs"] = ref_bundle["sections"]["response"]["references"]
        return analysis

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
        open_items = json.loads(row.get("open_items_json") or "[]")
        executive_summary = row.get("executive_summary") or ""
        response = row.get("response") or ""

        if not executive_summary and response:
            parsed = parse_assessment(response)
            executive_summary = parsed["executive_summary"]
            if not open_items:
                open_items = parsed["open_items"]

        return {
            "id": row["id"],
            "query": row["query"],
            "response": response,
            "document_ids": json.loads(row.get("document_ids_json") or "[]"),
            "model": row.get("model"),
            "analysis_type": row.get("analysis_type") or "query",
            "executive_summary": executive_summary,
            "open_items": open_items,
            "created_at": row["created_at"],
        }
