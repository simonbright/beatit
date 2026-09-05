"""Live multi-turn chat focused on deep analysis of clinical treatment options."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from app.services.content_policy import filter_palliative_content
from app.services.llm import LLMClient
from app.services.source_policy import SOURCE_ATTRIBUTION_RULES
from app.services.synthesis import (
    _format_corpus,
    _format_document_inventory,
    build_medical_system_prompt,
)
from app.storage.database import Database
from app.storage.documents import DocumentStore

OPTIONS_CHAT_RULES = """
OPTIONS CHAT MODE (live guided discussion):
- Your job is deep analysis of clinical OPTIONS for this case — surgery, systemic therapy, radiation, clinical trials, molecular testing, staging workup, and sequencing.
- Follow the user's lead. When they ask to go deeper on one option, regimen, trial class, biomarker, or trade-off, do so thoroughly.
- Prefer structured comparisons when helpful: eligibility, evidence strength, practical requirements, unknowns, and what would change the recommendation.
- Separate what is documented in the chart from guideline/general specialty knowledge and from AI inference.
- Do not invent labs, imaging, staging, or biomarker results that are absent from STORED DOCUMENTS / CURRENT ASSESSMENT / FOCUS DOCUMENTS.
- When FOCUS DOCUMENTS are provided, prioritize those; they were explicitly requested by the user.
- Ask a brief clarifying question when a fork in the analysis would materially change the option set — but still give a useful partial answer.
- Keep responses conversational enough for live guidance, but clinically precise. Use short headings and bullets when comparing options.
- Cite claims with [SOURCE: …] tags using the source attribution rules below.
- This chat does NOT automatically replace the Home assessment. When the user wants the main assessment updated, tell them to use “Update Home assessment” (or pin excerpts and run Update analysis on Home).
"""

OPTIONS_STARTER_PROMPTS = [
    "Compare the realistic treatment options for this case given current staging and workup.",
    "Go deep on systemic therapy options — regimens, sequencing, and what biomarkers would change the choice.",
    "What clinical trials or investigational approaches could fit this patient, and what eligibility gaps remain?",
    "Walk through surgical vs non-surgical pathways and the decision points between them.",
    "List the molecular / staging tests that would most change the option set, ranked by impact.",
]

_READ_DOC_HINT = re.compile(
    r"\b(read|open|look at|review|check|summarize|analyse|analyze|extract|ocr)\b",
    re.I,
)


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def match_documents_for_message(
    message: str,
    documents: list[dict[str, Any]],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Find library documents the user is asking about by title."""
    text = (message or "").strip()
    if not text or not documents:
        return []

    lowered = text.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in documents:
        title = (doc.get("title") or "").strip()
        display = (doc.get("citation_display_name") or "").strip()
        candidates = [c for c in (title, display) if c]
        best = 0
        for cand in candidates:
            norm = _normalize_title(cand)
            if len(norm) < 4:
                continue
            if norm in _normalize_title(text) or cand.lower() in lowered:
                best = max(best, 100 + len(norm))
                continue
            # Token overlap for partial matches (e.g. "calcium scan")
            tokens = [t for t in norm.split() if len(t) > 3]
            if not tokens:
                continue
            hits = sum(1 for t in tokens if t in lowered)
            if hits and hits >= max(1, len(tokens) // 2):
                best = max(best, 40 + hits * 10)
        if best:
            # Prefer explicit read/review intent
            if _READ_DOC_HINT.search(text):
                best += 15
            scored.append((best, doc))

    scored.sort(key=lambda item: (-item[0], item[1].get("title") or ""))
    seen: set[str] = set()
    matched: list[dict[str, Any]] = []
    for _, doc in scored:
        doc_id = doc.get("id")
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        matched.append(doc)
        if len(matched) >= limit:
            break
    return matched


class OptionsChatService:
    def __init__(self, store: DocumentStore, db: Database, llm: LLMClient):
        self.store = store
        self.db = db
        self.llm = llm

    async def create_session(
        self,
        *,
        document_ids: list[str] | None = None,
        include_latest_assessment: bool = True,
        title: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        return await self.db.create_chat_session(
            title=title or "AI Chat",
            focus="options",
            document_ids=document_ids or [],
            include_latest_assessment=include_latest_assessment,
            created_by=created_by,
        )

    async def get_session_bundle(self, session_id: str) -> dict[str, Any] | None:
        session = await self.db.get_chat_session(session_id)
        if not session:
            return None
        messages = await self.db.list_chat_messages(session_id)
        return {"session": session, "messages": messages}

    async def _focus_documents_block(
        self,
        message: str,
        session: dict[str, Any],
    ) -> str:
        from app.services.patient_documents import list_patient_documents
        from app.services.case_manager import get_active_context

        doc_ids = session.get("document_ids") or None
        if doc_ids == []:
            doc_ids = None
        ctx = get_active_context()
        patient_id = ctx.get("patient_id")
        if patient_id:
            docs = await list_patient_documents(
                patient_id,
                active_case_id=ctx.get("case_id"),
            )
        else:
            docs = await self.db.list_documents()
        if doc_ids is not None:
            allowed = set(doc_ids)
            docs = [d for d in docs if d.get("id") in allowed]
        matched = match_documents_for_message(message, docs)
        if not matched:
            return ""

        parts: list[str] = [
            "=== FOCUS DOCUMENTS (explicitly requested this turn — use these first) ==="
        ]
        for doc in matched:
            text = await self.store.read_extracted_text(doc) or ""
            title = doc.get("citation_display_name") or doc.get("title") or doc.get("id")
            case_label = doc.get("case_label")
            method = (doc.get("metadata") or {}).get("extraction_method")
            needs_ocr = (doc.get("metadata") or {}).get("needs_ocr")
            note = ""
            if needs_ocr or (text.startswith("[No extractable text")):
                # Try OCR ourselves before asking the user
                try:
                    from app.ingest.pdf import reextract_pdf_document
                    from app.services.clinical_report_handling import open_store_for_patient_document

                    ctx = get_active_context()
                    pid = ctx.get("patient_id")
                    opened = (
                        await open_store_for_patient_document(
                            pid,
                            doc["id"],
                            active_case_id=ctx.get("case_id"),
                        )
                        if pid
                        else None
                    )
                    if opened:
                        case_store, raw = opened
                        updated = await reextract_pdf_document(case_store, raw)
                        text = await case_store.read_extracted_text(updated) or text
                        method = (updated.get("metadata") or {}).get("extraction_method")
                        needs_ocr = (updated.get("metadata") or {}).get("needs_ocr")
                        doc = {**doc, **updated}
                except Exception:
                    pass
            if needs_ocr or (text.startswith("[No extractable text")):
                note = (
                    "\n[WARNING: This PDF still has little/no extractable text after automatic OCR. "
                    "The scan may be unreadable, or Replace file may be needed if the PDF is missing.]"
                )
            elif method == "ocr":
                note = "\n[Text recovered via OCR.]"
            body = text.strip() or "[No extracted text on file]"
            if len(body) > 40_000:
                body = body[:40_000] + "\n…[truncated]"
            case_line = f"\nFocus: {case_label}" if case_label else ""
            parts.append(
                f"--- FOCUS: {title} ---\n"
                f"ID: {doc.get('id')}\n"
                f"Type: {doc.get('source_type')}"
                f"{case_line}"
                f"{note}\n\n{body}"
            )
        return "\n\n".join(parts)

    async def _build_system_prompt(
        self,
        session: dict[str, Any],
        *,
        user_message: str | None = None,
    ) -> str:
        medical = await build_medical_system_prompt(self.db)
        doc_ids = session.get("document_ids") or None
        if doc_ids == []:
            # Empty list means "use all documents" (same convention as analysis).
            doc_ids = None

        corpus = await self.store.get_corpus(doc_ids)
        # _format_corpus re-prepares; pass original corpus for consistent inventory.
        corpus_text, coverage = _format_corpus(corpus, max_chars=120_000)
        inventory = _format_document_inventory(corpus)

        assessment_block = ""
        if session.get("include_latest_assessment", True):
            latest = await self.db.get_latest_analysis()
            if latest and (latest.get("executive_summary") or latest.get("response")):
                summary = (latest.get("executive_summary") or "").strip()
                body = (latest.get("response") or "").strip()
                # Keep prior assessment lean so chat has room to go deep.
                if len(body) > 12_000:
                    body = body[:12_000] + "\n…[truncated]"
                assessment_block = (
                    "\n\n=== CURRENT HOME ASSESSMENT (context for options discussion) ===\n"
                    f"Assessment date: {latest.get('created_at') or 'unknown'}\n"
                    f"Executive summary:\n{summary or '(none)'}\n\n"
                    f"Full assessment excerpt:\n{body or '(none)'}\n"
                )

        focus_block = ""
        if user_message:
            focus_block = await self._focus_documents_block(user_message, session)
            if focus_block:
                focus_block = f"\n\n{focus_block}\n"

        return (
            f"{medical}\n\n{OPTIONS_CHAT_RULES}\n\n{SOURCE_ATTRIBUTION_RULES}\n"
            f"{assessment_block}\n"
            f"{focus_block}"
            f"=== DOCUMENT INVENTORY ===\n{inventory}\n\n"
            f"=== COVERAGE NOTES ===\n{coverage or '- None'}\n\n"
            f"=== STORED DOCUMENTS ===\n{corpus_text or '[No documents in scope]'}\n"
        )

    def _history_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for row in rows:
            role = row.get("role")
            content = (row.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                history.append({"role": role, "content": content})
        # Keep recent turns if history grows large.
        if len(history) > 40:
            history = history[-40:]
        return history

    async def send_message(
        self,
        session_id: str,
        content: str,
        *,
        stream: bool = False,
    ) -> dict[str, Any] | AsyncIterator[dict[str, Any]]:
        text = (content or "").strip()
        if not text:
            raise ValueError("Message cannot be empty")

        session = await self.db.get_chat_session(session_id)
        if not session:
            raise ValueError("Chat session not found")

        prior = await self.db.list_chat_messages(session_id)
        user_msg = await self.db.insert_chat_message(
            session_id=session_id,
            role="user",
            content=text,
        )

        if len(prior) == 0 and session.get("title") in {
            "AI Chat",
            "Options chat",
            "New options chat",
        }:
            title = text if len(text) <= 80 else text[:77] + "…"
            await self.db.update_chat_session(session_id, title=title)
            session = await self.db.get_chat_session(session_id) or session

        system = await self._build_system_prompt(session, user_message=text)
        messages = [
            {"role": "system", "content": system},
            *self._history_messages(prior),
            {"role": "user", "content": text},
        ]

        if stream:
            return self._stream_reply(session_id, messages, user_msg)

        raw = await self.llm.chat(messages=messages, temperature=0.25)
        cleaned = filter_palliative_content(raw)
        assistant = await self.db.insert_chat_message(
            session_id=session_id,
            role="assistant",
            content=cleaned,
            model=self.llm.model_name,
        )
        refreshed = await self.get_session_bundle(session_id)
        return {
            "session": refreshed["session"] if refreshed else session,
            "user_message": user_msg,
            "assistant_message": assistant,
        }

    async def _stream_reply(
        self,
        session_id: str,
        messages: list[dict[str, str]],
        user_msg: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "start", "session_id": session_id, "user_message": user_msg}
        chunks: list[str] = []
        try:
            async for token in self.llm.stream_chat(messages=messages, temperature=0.25):
                if not token:
                    continue
                chunks.append(token)
                yield {"type": "token", "content": token}
            raw = "".join(chunks).strip()
            if not raw:
                raise RuntimeError("Model returned an empty reply")
            cleaned = filter_palliative_content(raw)
            assistant = await self.db.insert_chat_message(
                session_id=session_id,
                role="assistant",
                content=cleaned,
                model=self.llm.model_name,
            )
            refreshed = await self.get_session_bundle(session_id)
            yield {
                "type": "done",
                "assistant_message": assistant,
                "session": refreshed["session"] if refreshed else None,
            }
        except Exception as exc:
            yield {"type": "error", "error": str(exc)}
