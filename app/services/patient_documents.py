"""Patient-wide document catalog (read across all cases for one patient).

Writes (ingest, delete, re-extract) stay on the active case DocumentStore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.case_manager import (
    _case_dir,
    get_active_context,
    list_cases,
    load_registry,
)
from app.storage.database import Database


def list_patient_case_dirs(patient_id: str) -> list[dict[str, Any]]:
    """Return [{id, label, dir}] for every case belonging to the patient."""
    cases = list_cases(patient_id)
    result: list[dict[str, Any]] = []
    for c in cases:
        case_id = c["id"]
        result.append(
            {
                "id": case_id,
                "label": c.get("label") or case_id,
                "dir": _case_dir(patient_id, case_id),
            }
        )
    return result


def _annotate_doc(
    doc: dict[str, Any],
    *,
    case_id: str,
    case_label: str,
    active_case_id: str | None,
) -> dict[str, Any]:
    out = dict(doc)
    out["case_id"] = case_id
    out["case_label"] = case_label
    out["is_active_case"] = case_id == active_case_id
    meta = dict(out.get("metadata") or {})
    meta["case_id"] = case_id
    meta["case_label"] = case_label
    out["metadata"] = meta
    return out


async def list_patient_documents(
    patient_id: str,
    *,
    active_case_id: str | None = None,
    source_type: str | None = None,
) -> list[dict[str, Any]]:
    """List documents from every case for this patient, annotated with case provenance."""
    if active_case_id is None:
        ctx = get_active_context()
        if ctx.get("patient_id") == patient_id:
            active_case_id = ctx.get("case_id")

    collected: list[dict[str, Any]] = []
    for case in list_patient_case_dirs(patient_id):
        db_path = Path(case["dir"]) / "beatit.db"
        if not db_path.exists():
            continue
        db = Database(db_path=db_path)
        docs = await db.list_documents(source_type=source_type)
        for doc in docs:
            collected.append(
                _annotate_doc(
                    doc,
                    case_id=case["id"],
                    case_label=case["label"],
                    active_case_id=active_case_id,
                )
            )

    collected.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return collected


async def resolve_patient_documents(
    patient_id: str,
    document_ids: list[str] | None = None,
    *,
    active_case_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return full document rows for the patient.

    If document_ids is None or empty, return all patient documents.
    """
    all_docs = await list_patient_documents(
        patient_id,
        active_case_id=active_case_id,
    )
    if not document_ids:
        return all_docs
    id_set = set(document_ids)
    return [d for d in all_docs if d.get("id") in id_set]


async def find_patient_document(
    patient_id: str,
    doc_id: str,
    *,
    active_case_id: str | None = None,
) -> dict[str, Any] | None:
    docs = await resolve_patient_documents(
        patient_id,
        [doc_id],
        active_case_id=active_case_id,
    )
    return docs[0] if docs else None


async def patient_document_type_counts(patient_id: str) -> dict[str, int]:
    docs = await list_patient_documents(patient_id)
    counts: dict[str, int] = {}
    for doc in docs:
        key = str(doc.get("source_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


async def get_patient_corpus(
    patient_id: str,
    document_ids: list[str] | None = None,
    *,
    active_case_id: str | None = None,
) -> list[dict[str, Any]]:
    """Build extracted-text corpus across all of a patient's cases."""
    docs = await resolve_patient_documents(
        patient_id,
        document_ids,
        active_case_id=active_case_id,
    )
    corpus: list[dict[str, Any]] = []
    for doc in docs:
        extracted_path = doc.get("extracted_path")
        if not extracted_path:
            continue
        path = Path(extracted_path)
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if not text:
            continue
        meta = dict(doc.get("metadata") or {})
        meta["case_id"] = doc.get("case_id")
        meta["case_label"] = doc.get("case_label")
        corpus.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "source_type": doc["source_type"],
                "source_uri": doc.get("source_uri"),
                "text": text,
                "metadata": meta,
                "case_id": doc.get("case_id"),
                "case_label": doc.get("case_label"),
            }
        )
    return corpus


async def get_active_patient_corpus(document_ids: list[str] | None = None) -> list[dict[str, Any]] | None:
    """Corpus for the active patient, or None if no active patient (caller falls back)."""
    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    if not patient_id:
        return None
    return await get_patient_corpus(
        patient_id,
        document_ids,
        active_case_id=ctx.get("case_id"),
    )


def _to_index_row(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": doc["id"],
        "title": doc.get("title"),
        "source_type": doc.get("source_type"),
        "source_uri": doc.get("source_uri"),
        "citation_display_name": doc.get("citation_display_name"),
        "metadata": doc.get("metadata") or {},
        "created_at": doc.get("created_at"),
        "case_id": doc.get("case_id"),
        "case_label": doc.get("case_label"),
        "is_active_case": bool(doc.get("is_active_case")),
    }


async def list_active_patient_document_index() -> dict[str, Any] | None:
    """Patient-wide index payload, or None if no active patient."""
    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    if not patient_id:
        return None
    docs = await list_patient_documents(
        patient_id,
        active_case_id=ctx.get("case_id"),
    )
    counts: dict[str, int] = {}
    for doc in docs:
        key = str(doc.get("source_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
        meta = doc.get("metadata") or {}
        kind = str(meta.get("clinical_report_kind") or "").strip().lower()
        if kind and kind != "unknown":
            kind_key = f"kind:{kind}"
            counts[kind_key] = counts.get(kind_key, 0) + 1
    return {
        "documents": [_to_index_row(d) for d in docs],
        "total": len(docs),
        "counts_by_type": counts,
    }


async def list_active_patient_documents_page(
    *,
    limit: int,
    offset: int,
    source_type: str | None = None,
) -> dict[str, Any] | None:
    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    if not patient_id:
        return None
    clinical_kind = None
    page_source_type = source_type
    if source_type and source_type.startswith("kind:"):
        clinical_kind = source_type[5:].strip().lower() or None
        page_source_type = None
    all_docs = await list_patient_documents(
        patient_id,
        active_case_id=ctx.get("case_id"),
    )
    counts: dict[str, int] = {}
    for doc in all_docs:
        key = str(doc.get("source_type") or "unknown")
        counts[key] = counts.get(key, 0) + 1
        kind = str((doc.get("metadata") or {}).get("clinical_report_kind") or "").strip().lower()
        if kind and kind != "unknown":
            kind_key = f"kind:{kind}"
            counts[kind_key] = counts.get(kind_key, 0) + 1
    filtered = all_docs
    if page_source_type:
        filtered = [
            d
            for d in filtered
            if str(d.get("source_type") or "").lower() == page_source_type.lower()
        ]
    if clinical_kind:
        filtered = [
            d
            for d in filtered
            if str((d.get("metadata") or {}).get("clinical_report_kind") or "").lower()
            == clinical_kind
        ]
    page = filtered[offset : offset + limit]
    return {
        "documents": page,
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
        "source_type": source_type,
        "counts_by_type": counts,
    }


async def resolve_active_patient_document(doc_id: str) -> dict[str, Any] | None:
    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    if not patient_id:
        return None
    return await find_patient_document(
        patient_id,
        doc_id,
        active_case_id=ctx.get("case_id"),
    )


def active_patient_id() -> str | None:
    reg = load_registry()
    return reg.get("active_patient")
