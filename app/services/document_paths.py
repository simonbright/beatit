"""Locate and heal stored document / extracted-text paths.

After patient-case migration (or disk remounts), DB rows may still point at
legacy absolute paths while files live under the active case directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings


def _basename_candidates(doc: dict[str, Any], *, kind: str) -> list[str]:
    """Possible filenames for a document's raw file or extracted text."""
    names: list[str] = []
    stored = str(doc.get("file_path" if kind == "file" else "extracted_path") or "").strip()
    if stored:
        names.append(Path(stored).name)
    doc_id = str(doc.get("id") or "").strip()
    if kind == "extracted" and doc_id:
        names.append(f"{doc_id}.txt")
    if kind == "file" and doc_id:
        meta = doc.get("metadata") or {}
        original = str(meta.get("original_filename") or "").strip()
        if original:
            names.append(f"{doc_id}_{Path(original).name}")
        title = str(doc.get("title") or "").strip()
        if title and title.lower().endswith(".pdf"):
            names.append(f"{doc_id}_{Path(title).name}")
    # Preserve order, drop empties/dupes
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _search_roots() -> list[Path]:
    """Search only the active patient (and legacy flat dirs) — never every patient."""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    add(settings.documents_dir)
    add(settings.extracted_dir)
    add(settings.data_dir / "documents")
    add(settings.data_dir / "extracted")

    try:
        from app.services.case_manager import get_active_context
        from app.services.patient_documents import list_patient_case_dirs

        ctx = get_active_context()
        pid = ctx.get("patient_id")
        if pid:
            for case in list_patient_case_dirs(pid):
                case_dir = Path(case["dir"])
                add(case_dir / "documents")
                add(case_dir / "extracted")
    except Exception:
        pass

    return roots


def _find_by_names(names: list[str], *, prefer_dirs: list[Path] | None = None) -> Path | None:
    if not names:
        return None
    roots = list(prefer_dirs or []) + _search_roots()
    seen_roots: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        if not root.is_dir():
            continue
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
        # Prefix match: {doc_id}_*
        for name in names:
            stem = name.split("_", 1)[0]
            if len(stem) < 8:
                continue
            for hit in root.glob(f"{stem}_*"):
                if hit.is_file():
                    return hit
    return None


def resolve_document_file_path(
    doc: dict[str, Any],
    *,
    prefer_dirs: list[Path] | None = None,
) -> Path | None:
    """Return an existing path for the stored original file, if any."""
    stored = str(doc.get("file_path") or "").strip()
    if stored:
        path = Path(stored)
        if path.is_file():
            return path
    names = _basename_candidates(doc, kind="file")
    prefer = list(prefer_dirs or []) + [settings.documents_dir, settings.data_dir / "documents"]
    return _find_by_names(names, prefer_dirs=prefer)


def resolve_extracted_path(
    doc: dict[str, Any],
    *,
    prefer_dirs: list[Path] | None = None,
) -> Path | None:
    stored = str(doc.get("extracted_path") or "").strip()
    if stored:
        path = Path(stored)
        if path.is_file():
            return path
    names = _basename_candidates(doc, kind="extracted")
    prefer = list(prefer_dirs or []) + [settings.extracted_dir, settings.data_dir / "extracted"]
    return _find_by_names(names, prefer_dirs=prefer)


async def heal_document_paths(store: Any, doc: dict[str, Any]) -> dict[str, Any]:
    """If files exist under a different root, rewrite DB paths and return updated doc."""
    prefer_docs = []
    prefer_ext = []
    if hasattr(store, "documents_dir"):
        prefer_docs.append(store.documents_dir)
    if hasattr(store, "extracted_dir"):
        prefer_ext.append(store.extracted_dir)
    file_path = resolve_document_file_path(doc, prefer_dirs=prefer_docs or None)
    extracted_path = resolve_extracted_path(doc, prefer_dirs=prefer_ext or None)
    updates: dict[str, str] = {}
    if file_path and str(file_path) != str(doc.get("file_path") or ""):
        updates["file_path"] = str(file_path)
    if extracted_path and str(extracted_path) != str(doc.get("extracted_path") or ""):
        updates["extracted_path"] = str(extracted_path)
    if not updates:
        return doc
    await store.db.update_document_paths(
        doc["id"],
        file_path=updates.get("file_path"),
        extracted_path=updates.get("extracted_path"),
    )
    updated = await store.db.get_document(doc["id"])
    if updated:
        # Preserve cross-case annotations if present
        for key in ("case_id", "case_label", "is_active_case", "source_info", "handling"):
            if key in doc and key not in updated:
                updated[key] = doc[key]
        return updated
    return {**doc, **updates}
