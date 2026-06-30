from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
PDF_EXTENSIONS = {".pdf"}


def _file_extension(doc: dict[str, Any]) -> str:
    meta = doc.get("metadata") or {}
    ext = (meta.get("file_extension") or "").lower()
    if ext:
        return ext if ext.startswith(".") else f".{ext}"
    file_path = doc.get("file_path") or ""
    if file_path.lower().endswith(".nii.gz"):
        return ".nii.gz"
    return Path(file_path).suffix.lower()


def file_is_available(doc: dict[str, Any]) -> bool:
    file_path = doc.get("file_path")
    return bool(file_path and Path(file_path).is_file())


def guess_media_type(filename: str, source_type: str | None = None) -> str:
    lower = filename.lower()
    if lower.endswith(".nii.gz"):
        return "application/octet-stream"
    ext = Path(lower).suffix
    if ext in PDF_EXTENSIONS or source_type == "pdf":
        return "application/pdf"
    if ext in {".dcm", ".dicom"}:
        return "application/dicom"
    if ext in IMAGE_EXTENSIONS:
        mapping = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
        }
        return mapping.get(ext, "image/jpeg")
    if ext in VIDEO_EXTENSIONS or source_type == "video":
        mapping = {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
            ".mkv": "video/x-matroska",
            ".avi": "video/x-msvideo",
            ".m4v": "video/mp4",
        }
        return mapping.get(ext, "application/octet-stream")
    if ext == ".zip":
        return "application/zip"
    guessed, _ = __import__("mimetypes").guess_type(filename)
    return guessed or "application/octet-stream"


def build_document_view(doc: dict[str, Any]) -> dict[str, Any]:
    doc_id = doc["id"]
    has_file = file_is_available(doc)
    file_url = f"/api/documents/{doc_id}/file" if has_file else None
    source_url = doc.get("source_uri")
    ext = _file_extension(doc)
    source_type = doc.get("source_type") or ""

    if source_url and source_type in {"url", "youtube"} and not has_file:
        return {
            "has_file": False,
            "file_url": None,
            "view_kind": "url",
            "source_url": source_url,
        }

    if not has_file:
        return {
            "has_file": False,
            "file_url": None,
            "view_kind": "text",
            "source_url": source_url,
        }

    if source_type == "pdf" or ext in PDF_EXTENSIONS:
        view_kind = "pdf"
    elif source_type == "video" or ext in VIDEO_EXTENSIONS:
        view_kind = "video"
    elif ext in IMAGE_EXTENSIONS:
        view_kind = "image"
    else:
        view_kind = "download"

    return {
        "has_file": True,
        "file_url": file_url,
        "view_kind": view_kind,
        "source_url": source_url,
        "media_type": guess_media_type(Path(doc["file_path"]).name, source_type),
    }
