"""Audit trail helpers — event types, logging, and display formatting."""

from typing import Any

DOCUMENT_CREATED = "document.created"
DOCUMENT_DELETED = "document.deleted"
SETTINGS_MODEL_UPDATED = "settings.model_updated"
SETTINGS_PATIENT_CONTEXT_UPDATED = "settings.patient_context_updated"
ANALYSIS_REQUESTED = "analysis.requested"
ANALYSIS_COMPLETED = "analysis.completed"
ANALYSIS_FAILED = "analysis.failed"
OPEN_ITEM_COMMENT_ADDED = "open_item.comment_added"
OPEN_ITEM_STATUS_CHANGED = "open_item.status_changed"
OPEN_ITEM_INVESTIGATION_STARTED = "open_item.investigation_started"
OPEN_ITEM_INVESTIGATION_DRAFT_CREATED = "open_item.investigation_draft_created"
OPEN_ITEM_INVESTIGATION_FAILED = "open_item.investigation_failed"
OPEN_ITEM_INVESTIGATION_ACCEPTED = "open_item.investigation_accepted"
OPEN_ITEM_INVESTIGATION_DISCARDED = "open_item.investigation_discarded"
OPEN_ITEM_INVESTIGATION_DRAFT_COMMENTED = "open_item.investigation_draft_commented"
AUTH_LOGIN = "auth.login"
AUTH_LOGOUT = "auth.logout"
PDF_EXPORTED = "pdf.exported"

EVENT_LABELS: dict[str, str] = {
    DOCUMENT_CREATED: "Document added",
    DOCUMENT_DELETED: "Document removed",
    SETTINGS_MODEL_UPDATED: "LLM model changed",
    SETTINGS_PATIENT_CONTEXT_UPDATED: "Patient context updated",
    ANALYSIS_REQUESTED: "Analysis requested",
    ANALYSIS_COMPLETED: "Analysis completed",
    ANALYSIS_FAILED: "Analysis failed",
    OPEN_ITEM_COMMENT_ADDED: "Open item comment",
    OPEN_ITEM_STATUS_CHANGED: "Open item status changed",
    OPEN_ITEM_INVESTIGATION_STARTED: "Investigation started",
    OPEN_ITEM_INVESTIGATION_DRAFT_CREATED: "Investigation draft created",
    OPEN_ITEM_INVESTIGATION_FAILED: "Investigation failed",
    OPEN_ITEM_INVESTIGATION_ACCEPTED: "Investigation accepted",
    OPEN_ITEM_INVESTIGATION_DISCARDED: "Investigation draft discarded",
    OPEN_ITEM_INVESTIGATION_DRAFT_COMMENTED: "Investigation saved as comment",
    AUTH_LOGIN: "Signed in",
    AUTH_LOGOUT: "Signed out",
    PDF_EXPORTED: "Assessment PDF exported",
}

CATEGORY_PREFIXES: dict[str, tuple[str, ...]] = {
    "documents": (DOCUMENT_CREATED, DOCUMENT_DELETED),
    "open_items": (
        OPEN_ITEM_COMMENT_ADDED,
        OPEN_ITEM_STATUS_CHANGED,
        OPEN_ITEM_INVESTIGATION_STARTED,
        OPEN_ITEM_INVESTIGATION_DRAFT_CREATED,
        OPEN_ITEM_INVESTIGATION_FAILED,
        OPEN_ITEM_INVESTIGATION_ACCEPTED,
        OPEN_ITEM_INVESTIGATION_DISCARDED,
        OPEN_ITEM_INVESTIGATION_DRAFT_COMMENTED,
    ),
    "analysis": (ANALYSIS_REQUESTED, ANALYSIS_COMPLETED, ANALYSIS_FAILED, PDF_EXPORTED),
    "settings": (SETTINGS_MODEL_UPDATED, SETTINGS_PATIENT_CONTEXT_UPDATED),
    "auth": (AUTH_LOGIN, AUTH_LOGOUT),
}


def preview_text(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


async def log_audit(
    db: Any,
    event_type: str,
    *,
    actor: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await db.insert_audit_event(
        event_type=event_type,
        actor=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata or {},
    )


def enrich_audit_event(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata") or {}
    enriched = dict(event)
    enriched["label"] = EVENT_LABELS.get(event.get("event_type", ""), event.get("event_type", "Event"))
    enriched["summary"] = format_audit_summary(event)
    enriched["details"] = format_audit_details(event)
    return enriched


def format_audit_summary(event: dict[str, Any]) -> str:
    event_type = event.get("event_type", "")
    meta = event.get("metadata") or {}

    if event_type == DOCUMENT_CREATED:
        return f'Added "{meta.get("title", "document")}" ({meta.get("source_type", "unknown")})'
    if event_type == DOCUMENT_DELETED:
        return f'Removed "{meta.get("title", "document")}" ({meta.get("source_type", "unknown")})'
    if event_type == SETTINGS_MODEL_UPDATED:
        return f'Model: {meta.get("old_model") or "—"} → {meta.get("new_model") or "—"}'
    if event_type == SETTINGS_PATIENT_CONTEXT_UPDATED:
        return f'Patient context updated ({meta.get("old_length", 0)} → {meta.get("new_length", 0)} chars)'
    if event_type == ANALYSIS_REQUESTED:
        return f'{meta.get("job_type", "analysis")} job queued'
    if event_type == ANALYSIS_COMPLETED:
        return f'{meta.get("analysis_type", "analysis")} finished · {meta.get("open_items_count", 0)} open items'
    if event_type == ANALYSIS_FAILED:
        return meta.get("error_preview") or "Analysis job failed"
    if event_type == OPEN_ITEM_COMMENT_ADDED:
        return preview_text(meta.get("comment"), 120) or "Comment added"
    if event_type == OPEN_ITEM_STATUS_CHANGED:
        return f'{meta.get("old_status", "?")} → {meta.get("new_status", "?")}'
    if event_type == OPEN_ITEM_INVESTIGATION_STARTED:
        return preview_text(meta.get("item"), 100) or "Investigation started"
    if event_type == OPEN_ITEM_INVESTIGATION_DRAFT_CREATED:
        return preview_text(meta.get("item"), 100) or "Draft ready for review"
    if event_type == OPEN_ITEM_INVESTIGATION_FAILED:
        return meta.get("error_preview") or "Investigation failed"
    if event_type == OPEN_ITEM_INVESTIGATION_ACCEPTED:
        return preview_text(meta.get("item"), 100) or "Investigation accepted"
    if event_type == OPEN_ITEM_INVESTIGATION_DISCARDED:
        return preview_text(meta.get("item"), 100) or "Draft discarded"
    if event_type == OPEN_ITEM_INVESTIGATION_DRAFT_COMMENTED:
        return preview_text(meta.get("item"), 100) or "Draft saved as comment"
    if event_type == AUTH_LOGIN:
        return f'User {meta.get("username") or event.get("actor") or "unknown"} signed in'
    if event_type == AUTH_LOGOUT:
        return "User signed out"
    if event_type == PDF_EXPORTED:
        return f'Exported assessment {meta.get("analysis_id", "")[:8]}…'
    return event_type.replace(".", " ").replace("_", " ").title()


def format_audit_details(event: dict[str, Any]) -> list[str]:
    meta = event.get("metadata") or {}
    lines: list[str] = []
    event_type = event.get("event_type", "")

    def add(label: str, value: Any) -> None:
        if value is None or value == "":
            return
        lines.append(f"{label}: {value}")

    if event_type in {DOCUMENT_CREATED, DOCUMENT_DELETED}:
        add("Title", meta.get("title"))
        add("Type", meta.get("source_type"))
        add("Source", meta.get("source_uri"))
        add("Document ID", event.get("resource_id"))
    elif event_type == SETTINGS_MODEL_UPDATED:
        add("Previous model", meta.get("old_model"))
        add("New model", meta.get("new_model"))
    elif event_type == SETTINGS_PATIENT_CONTEXT_UPDATED:
        add("Previous length", meta.get("old_length"))
        add("New length", meta.get("new_length"))
        if meta.get("old_preview"):
            add("Previous preview", meta.get("old_preview"))
        if meta.get("new_preview"):
            add("New preview", meta.get("new_preview"))
    elif event_type in {ANALYSIS_REQUESTED, ANALYSIS_COMPLETED, ANALYSIS_FAILED}:
        add("Job type", meta.get("job_type") or meta.get("analysis_type"))
        add("Query", meta.get("query_preview"))
        add("Documents", meta.get("document_count"))
        add("Model", meta.get("model"))
        add("Analysis ID", meta.get("analysis_id"))
        add("Open items", meta.get("open_items_count"))
        if meta.get("error_preview"):
            add("Error", meta.get("error_preview"))
    elif event_type == OPEN_ITEM_COMMENT_ADDED:
        add("Item", meta.get("item"))
        add("Comment", meta.get("comment"))
    elif event_type == OPEN_ITEM_STATUS_CHANGED:
        add("Item", meta.get("item"))
        add("Previous status", meta.get("old_status"))
        add("New status", meta.get("new_status"))
    elif event_type.startswith("open_item.investigation"):
        add("Item", meta.get("item"))
        add("Type", meta.get("item_type"))
        add("Guidance", meta.get("guidance_preview"))
        add("Model", meta.get("model"))
        if meta.get("error_preview"):
            add("Error", meta.get("error_preview"))
    elif event_type == PDF_EXPORTED:
        add("Analysis ID", meta.get("analysis_id"))
        add("Filename", meta.get("filename"))
    elif event_type == AUTH_LOGIN:
        add("Username", meta.get("username") or event.get("actor"))

    if event.get("actor"):
        lines.insert(0, f"Actor: {event['actor']}")
    if event.get("resource_id") and event_type not in {DOCUMENT_CREATED, DOCUMENT_DELETED}:
        add("Resource ID", event.get("resource_id"))

    return lines
