from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from app.storage.documents import DocumentStore

ALLOWED_IMAGING_EXTENSIONS = {
    ".dcm",
    ".dicom",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".nii",
    ".nii.gz",
    ".mha",
    ".mhd",
    ".zip",
}

FORMAT_LABELS = {
    ".dcm": "DICOM",
    ".dicom": "DICOM",
    ".jpg": "JPEG image",
    ".jpeg": "JPEG image",
    ".png": "PNG image",
    ".gif": "GIF image",
    ".bmp": "Bitmap image",
    ".tif": "TIFF image",
    ".tiff": "TIFF image",
    ".webp": "WebP image",
    ".nii": "NIfTI volume",
    ".nii.gz": "NIfTI volume (gzip)",
    ".mha": "MetaImage",
    ".mhd": "MetaImage header",
    ".zip": "ZIP archive",
}


def imaging_extension(filename: str) -> str | None:
    lower = (filename or "").lower().replace("\\", "/")
    if lower.endswith(".nii.gz"):
        return ".nii.gz"
    ext = Path(lower).suffix
    if ext in ALLOWED_IMAGING_EXTENSIONS:
        return ext
    return None


def is_allowed_imaging_filename(filename: str) -> bool:
    return imaging_extension(filename) is not None


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.2f} GB"


def _extract_dicom_metadata(content: bytes) -> dict[str, str]:
    try:
        import pydicom

        dataset = pydicom.dcmread(BytesIO(content), stop_before_pixels=True, force=True)
    except Exception:
        return {}

    def _get(name: str) -> str:
        value = getattr(dataset, name, None)
        if value is None:
            return ""
        return str(value).strip()

    fields = {
        "modality": _get("Modality"),
        "study_date": _get("StudyDate"),
        "study_description": _get("StudyDescription"),
        "series_description": _get("SeriesDescription"),
        "series_number": _get("SeriesNumber"),
        "instance_number": _get("InstanceNumber"),
        "body_part": _get("BodyPartExamined"),
        "slice_thickness": _get("SliceThickness"),
    }
    return {key: value for key, value in fields.items() if value}


def _build_extracted_text(
    *,
    filename: str,
    ext: str,
    file_size: int,
    relative_path: str | None,
    notes: str | None,
    dicom_meta: dict[str, str],
) -> str:
    lines = [
        f"[Medical imaging file stored: {filename}]",
        f"Format: {FORMAT_LABELS.get(ext, ext.lstrip('.'))}",
        f"Size: {_format_bytes(file_size)}",
    ]
    if relative_path and relative_path != filename:
        lines.append(f"Folder path: {relative_path}")

    for label, key in (
        ("Modality", "modality"),
        ("Study date", "study_date"),
        ("Study", "study_description"),
        ("Series", "series_description"),
        ("Series number", "series_number"),
        ("Instance", "instance_number"),
        ("Body part", "body_part"),
        ("Slice thickness", "slice_thickness"),
    ):
        value = dicom_meta.get(key)
        if value:
            lines.append(f"{label}: {value}")

    lines.extend(
        [
            "",
            "Imaging pixels are stored locally and are not sent to the LLM automatically.",
            "Add a radiology report, summary text, or notes below for analysis.",
        ]
    )

    if notes and notes.strip():
        lines.extend(["", "=== UPLOAD NOTES ===", notes.strip()])

    return "\n".join(lines)


def build_imaging_title(
    *,
    filename: str,
    relative_path: str | None = None,
    title_prefix: str | None = None,
) -> str:
    path = (relative_path or filename or "imaging").replace("\\", "/")
    leaf = Path(path).name or filename or "imaging"
    if title_prefix and title_prefix.strip():
        prefix = title_prefix.strip()
        if path != leaf and "/" in path:
            return f"{prefix} — {path}"
        return f"{prefix} — {leaf}"
    return path if path != leaf or "/" not in path else leaf


async def ingest_imaging_file(
    store: DocumentStore,
    *,
    filename: str,
    content: bytes,
    title: str | None = None,
    relative_path: str | None = None,
    title_prefix: str | None = None,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ext = imaging_extension(filename)
    if not ext:
        allowed = ", ".join(sorted(ALLOWED_IMAGING_EXTENSIONS))
        raise ValueError(f"Unsupported imaging file type. Allowed: {allowed}")

    dicom_meta: dict[str, str] = {}
    if ext in {".dcm", ".dicom"}:
        dicom_meta = _extract_dicom_metadata(content)

    meta = dict(metadata or {})
    meta.update(
        {
            "original_filename": filename,
            "relative_path": relative_path or filename,
            "file_extension": ext,
            "file_size": len(content),
            "file_size_label": _format_bytes(len(content)),
            "imaging_format": FORMAT_LABELS.get(ext, ext.lstrip(".")),
        }
    )
    meta.update({f"dicom_{key}": value for key, value in dicom_meta.items()})
    if dicom_meta.get("modality"):
        meta["modality"] = dicom_meta["modality"]

    doc_title = title or build_imaging_title(
        filename=filename,
        relative_path=relative_path,
        title_prefix=title_prefix,
    )
    extracted = _build_extracted_text(
        filename=filename,
        ext=ext,
        file_size=len(content),
        relative_path=relative_path,
        notes=notes,
        dicom_meta=dicom_meta,
    )

    return await store.create_document(
        title=doc_title,
        source_type="imaging",
        extracted_text=extracted,
        raw_filename=filename,
        raw_content=content,
        metadata=meta,
    )
