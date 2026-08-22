"""Curated excerpts from AI Chat for integration into assessments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.storage.database import Database
from app.storage.documents import DocumentStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def save_observation_to_library(
    store: DocumentStore,
    db: Database,
    observation_id: str,
) -> dict[str, Any]:
    obs = await db.get_chat_observation(observation_id)
    if not obs:
        raise ValueError("Observation not found")
    if obs.get("document_id"):
        doc = await db.get_document(obs["document_id"])
        if doc:
            return {"document": doc, "observation": obs}

    metadata = {
        "chat_session_id": obs["session_id"],
        "chat_message_id": obs.get("message_id"),
        "chat_observation_id": obs["id"],
        "saved_at": _now_iso(),
    }
    doc = await store.create_document(
        title=obs["title"],
        source_type="chat_observation",
        extracted_text=obs["excerpt"],
        metadata=metadata,
    )
    await db.update_chat_observation(observation_id, document_id=doc["id"])
    updated_obs = await db.get_chat_observation(observation_id)
    return {"document": doc, "observation": updated_obs}


def format_chat_observations_for_prompt(observations: list[dict[str, Any]]) -> str:
    if not observations:
        return ""
    blocks: list[str] = []
    for obs in observations:
        title = (obs.get("title") or "Chat observation").strip()
        excerpt = (obs.get("excerpt") or "").strip()
        if not excerpt:
            continue
        blocks.append(
            f'--- Observation: "{title}" [cite as [SOURCE: Chat observation "{title}"]] ---\n'
            f"{excerpt}"
        )
    if not blocks:
        return ""
    header = (
        "=== CHAT OBSERVATIONS (user-curated from AI Chat — integrate where relevant) ===\n"
        "Each item may contain nested [SOURCE: …] tags. Preserve those when the claim is library-backed.\n"
        "Treat unattributed chat content as [SOURCE: AI inference — not verified] unless tied to a "
        "document tag inside the excerpt.\n"
    )
    return header + "\n\n".join(blocks)


async def resolve_observations_for_analysis(
    db: Database,
    *,
    observation_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if observation_ids:
        obs_list = await db.get_chat_observations_by_ids(observation_ids)
        return [o for o in obs_list if o.get("include_in_analysis")]
    return await db.list_chat_observations(
        include_in_analysis_only=True,
        pending_only=True,
    )


async def observation_library_document_ids(observations: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for obs in observations:
        doc_id = obs.get("document_id")
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            ids.append(doc_id)
    return ids
