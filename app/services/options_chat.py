"""Live multi-turn chat focused on deep analysis of clinical treatment options."""

from __future__ import annotations

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
- Separate what is documented in the chart from guideline/general oncology knowledge and from AI inference.
- Do not invent labs, imaging, staging, or biomarker results that are absent from STORED DOCUMENTS / CURRENT ASSESSMENT.
- Ask a brief clarifying question when a fork in the analysis would materially change the option set — but still give a useful partial answer.
- Keep responses conversational enough for live guidance, but clinically precise. Use short headings and bullets when comparing options.
- Cite claims with [SOURCE: …] tags using the source attribution rules below.
- This chat does NOT replace the Home assessment; it is for exploring options in depth.
"""

OPTIONS_STARTER_PROMPTS = [
    "Compare the realistic treatment options for this case given current staging and workup.",
    "Go deep on systemic therapy options — regimens, sequencing, and what biomarkers would change the choice.",
    "What clinical trials or investigational approaches could fit this patient, and what eligibility gaps remain?",
    "Walk through surgical vs non-surgical pathways and the decision points between them.",
    "List the molecular / staging tests that would most change the option set, ranked by impact.",
]


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

    async def _build_system_prompt(self, session: dict[str, Any]) -> str:
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

        return (
            f"{medical}\n\n{OPTIONS_CHAT_RULES}\n\n{SOURCE_ATTRIBUTION_RULES}\n"
            f"{assessment_block}\n"
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

        system = await self._build_system_prompt(session)
        messages = [{"role": "system", "content": system}, *self._history_messages(prior), {"role": "user", "content": text}]

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
