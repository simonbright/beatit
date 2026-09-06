"""Audit trail helpers — event types, logging, and display formatting."""

from typing import Any

DOCUMENT_CREATED = "document.created"
DOCUMENT_DELETED = "document.deleted"
SETTINGS_MODEL_UPDATED = "settings.model_updated"
SETTINGS_PATIENT_CONTEXT_UPDATED = "settings.patient_context_updated"
SETTINGS_REVIEWER_CONTEXT_UPDATED = "settings.reviewer_context_updated"
SETTINGS_SOURCE_LABELS_UPDATED = "settings.source_labels_updated"
DOCUMENT_CITATION_UPDATED = "document.citation_updated"
ANALYSIS_REQUESTED = "analysis.requested"
ANALYSIS_COMPLETED = "analysis.completed"
ANALYSIS_FAILED = "analysis.failed"
ANALYSIS_PROMOTED = "analysis.promoted"
ANALYSIS_DRAFT_DISCARDED = "analysis.draft_discarded"
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
AUTH_USER_UPSERTED = "auth.user_upserted"
AUTH_USER_DELETED = "auth.user_deleted"
PDF_EXPORTED = "pdf.exported"
ANALYSIS_ANNOTATIONS_UPDATED = "analysis.annotations_updated"
ANALYSIS_SHARED_EMAIL = "analysis.shared_email"

EVENT_LABELS: dict[str, str] = {
    DOCUMENT_CREATED: "Document added",
    DOCUMENT_DELETED: "Document removed",
    SETTINGS_MODEL_UPDATED: "LLM model changed",
    SETTINGS_PATIENT_CONTEXT_UPDATED: "Patient context updated",
    SETTINGS_REVIEWER_CONTEXT_UPDATED: "Clinical reviewer context updated",
    SETTINGS_SOURCE_LABELS_UPDATED: "Source labels updated",
    DOCUMENT_CITATION_UPDATED: "Document citation name updated",
    ANALYSIS_REQUESTED: "Analysis requested",
    ANALYSIS_COMPLETED: "Analysis completed",
    ANALYSIS_FAILED: "Analysis failed",
    ANALYSIS_PROMOTED: "Draft added to medical record",
    ANALYSIS_DRAFT_DISCARDED: "Draft discarded",
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
    AUTH_USER_UPSERTED: "Sign-in user added or updated",
    AUTH_USER_DELETED: "Sign-in user removed",
    PDF_EXPORTED: "Assessment PDF exported",
    ANALYSIS_ANNOTATIONS_UPDATED: "Custom task annotations updated",
    ANALYSIS_SHARED_EMAIL: "Custom task shared by email",
}

CATEGORY_PREFIXES: dict[str, tuple[str, ...]] = {
    "documents": (DOCUMENT_CREATED, DOCUMENT_DELETED, DOCUMENT_CITATION_UPDATED),
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
    "analysis": (
        ANALYSIS_REQUESTED,
        ANALYSIS_COMPLETED,
        ANALYSIS_FAILED,
        ANALYSIS_PROMOTED,
        ANALYSIS_DRAFT_DISCARDED,
        PDF_EXPORTED,
        ANALYSIS_ANNOTATIONS_UPDATED,
        ANALYSIS_SHARED_EMAIL,
    ),
    "settings": (
        SETTINGS_MODEL_UPDATED,
        SETTINGS_PATIENT_CONTEXT_UPDATED,
        SETTINGS_REVIEWER_CONTEXT_UPDATED,
        SETTINGS_SOURCE_LABELS_UPDATED,
    ),
    "auth": (AUTH_LOGIN, AUTH_LOGOUT, AUTH_USER_UPSERTED, AUTH_USER_DELETED),
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


def format_audit_actor(event: dict[str, Any]) -> str | None:
    actor = event.get("actor")
    if actor:
        return str(actor)
    meta = event.get("metadata") or {}
    username = meta.get("username")
    if username:
        return str(username)
    return None


def enrich_audit_event(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata") or {}
    enriched = dict(event)
    enriched["label"] = EVENT_LABELS.get(event.get("event_type", ""), event.get("event_type", "Event"))
    enriched["actor_display"] = format_audit_actor(event)
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
    if event_type == DOCUMENT_CITATION_UPDATED:
        old_name = meta.get("old_display_name") or meta.get("old_title") or "—"
        new_name = meta.get("new_display_name") or meta.get("new_title") or "—"
        return f'Citation name: {old_name} → {new_name}'
    if event_type == SETTINGS_MODEL_UPDATED:
        return f'Model: {meta.get("old_model") or "—"} → {meta.get("new_model") or "—"}'
    if event_type == SETTINGS_PATIENT_CONTEXT_UPDATED:
        return f'Patient context updated ({meta.get("old_length", 0)} → {meta.get("new_length", 0)} chars)'
    if event_type == SETTINGS_REVIEWER_CONTEXT_UPDATED:
        return f'Clinical reviewer context updated ({meta.get("old_length", 0)} → {meta.get("new_length", 0)} chars)'
    if event_type == SETTINGS_SOURCE_LABELS_UPDATED:
        return meta.get("summary") or "Source type labels updated"
    if event_type == ANALYSIS_REQUESTED:
        return f'{meta.get("job_type", "analysis")} job queued'
    if event_type == ANALYSIS_COMPLETED:
        status = meta.get("record_status", "official")
        if status == "draft":
            return f'Custom task draft ready · {preview_text(meta.get("query_preview"), 80) or "custom query"}'
        return f'{meta.get("analysis_type", "analysis")} finished · {meta.get("open_items_count", 0)} open items'
    if event_type == ANALYSIS_FAILED:
        return meta.get("error_preview") or "Analysis job failed"
    if event_type == ANALYSIS_PROMOTED:
        return preview_text(meta.get("query_preview"), 100) or "Draft promoted to medical record"
    if event_type == ANALYSIS_DRAFT_DISCARDED:
        return preview_text(meta.get("query_preview"), 100) or "Draft discarded"
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
        user = meta.get("username") or event.get("actor")
        return f'User {user} signed out' if user else "User signed out"
    if event_type == PDF_EXPORTED:
        label = meta.get("filename") or meta.get("analysis_id", "")[:8]
        return f"Exported PDF · {label}"
    if event_type == ANALYSIS_ANNOTATIONS_UPDATED:
        return preview_text(meta.get("annotation_title") or meta.get("query_preview"), 100) or "Annotations updated"
    if event_type == ANALYSIS_SHARED_EMAIL:
        recipient = meta.get("recipient") or "recipient"
        return f"Shared by email to {recipient}"
    return event_type.replace(".", " ").replace("_", " ").title()


def format_audit_details(event: dict[str, Any]) -> list[str]:
    meta = event.get("metadata") or {}
    lines: list[str] = []
    event_type = event.get("event_type", "")

    def add(label: str, value: Any) -> None:
        if value is None or value == "":
            return
        lines.append(f"{label}: {value}")

    if event_type in {DOCUMENT_CREATED, DOCUMENT_DELETED, DOCUMENT_CITATION_UPDATED}:
        add("Title", meta.get("title"))
        add("Type", meta.get("source_type"))
        add("Source", meta.get("source_uri"))
        add("Document ID", event.get("resource_id"))
        if event_type == DOCUMENT_CITATION_UPDATED:
            add("Previous citation name", meta.get("old_display_name"))
            add("New citation name", meta.get("new_display_name"))
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
    elif event_type == SETTINGS_REVIEWER_CONTEXT_UPDATED:
        add("Previous length", meta.get("old_length"))
        add("New length", meta.get("new_length"))
        if meta.get("old_preview"):
            add("Previous preview", meta.get("old_preview"))
        if meta.get("new_preview"):
            add("New preview", meta.get("new_preview"))
    elif event_type == SETTINGS_SOURCE_LABELS_UPDATED:
        if meta.get("changes"):
            add("Changes", meta.get("changes"))
    elif event_type in {ANALYSIS_REQUESTED, ANALYSIS_COMPLETED, ANALYSIS_FAILED, ANALYSIS_PROMOTED, ANALYSIS_DRAFT_DISCARDED}:
        add("Job type", meta.get("job_type") or meta.get("analysis_type"))
        add("Query", meta.get("query_preview"))
        add("Documents", meta.get("document_count"))
        add("Model", meta.get("model"))
        add("Analysis ID", meta.get("analysis_id"))
        add("Record status", meta.get("record_status"))
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
        add("Record status", meta.get("record_status"))
    elif event_type == ANALYSIS_ANNOTATIONS_UPDATED:
        add("Title", meta.get("annotation_title"))
        add("Generated by", meta.get("created_by"))
        add("Query", meta.get("query_preview"))
    elif event_type == ANALYSIS_SHARED_EMAIL:
        add("Recipient", meta.get("recipient"))
        add("Subject", meta.get("subject"))
        add("Generated by", meta.get("created_by"))
        add("Title", meta.get("annotation_title"))
        add("Delivery", meta.get("delivery"))
    elif event_type == AUTH_LOGIN:
        add("Username", meta.get("username") or event.get("actor"))
    elif event_type == AUTH_LOGOUT:
        add("Username", meta.get("username") or event.get("actor"))

    if event.get("resource_id") and event_type not in {DOCUMENT_CREATED, DOCUMENT_DELETED}:
        add("Resource ID", event.get("resource_id"))

    return lines
