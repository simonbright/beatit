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


def is_dicom_bytes(content: bytes) -> bool:
    """Detect DICOM by preamble or pydicom parse (handles extensionless slices)."""
    if not content:
        return False
    if len(content) >= 132 and content[128:132] == b"DICM":
        return True
    try:
        import pydicom

        pydicom.dcmread(BytesIO(content), stop_before_pixels=True, force=True)
        return True
    except Exception:
        return False


def resolve_imaging_type(filename: str, content: bytes) -> tuple[str, str] | None:
    ext = imaging_extension(filename)
    if ext:
        return ext, FORMAT_LABELS.get(ext, ext.lstrip("."))
    if is_dicom_bytes(content):
        return ".dcm", "DICOM"
    return None


def is_allowed_imaging_upload(filename: str, content: bytes) -> bool:
    return resolve_imaging_type(filename, content) is not None


def is_allowed_imaging_filename(filename: str) -> bool:
    return imaging_extension(filename) is not None


def storage_filename_for_upload(filename: str, ext: str) -> str:
    """Ensure stored files have a usable extension (many DICOM slices have none)."""
    name = Path(filename or "imaging").name
    if ext == ".dcm" and not imaging_extension(name):
        return f"{name}.dcm" if name else "imaging.dcm"
    return name or "imaging.dcm"


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
    resolved = resolve_imaging_type(filename, content)
    if not resolved:
        allowed = ", ".join(sorted(ALLOWED_IMAGING_EXTENSIONS))
        raise ValueError(
            f"Unsupported imaging file type. Allowed extensions: {allowed}. "
            "Extensionless DICOM slices are also accepted when the file content is valid DICOM."
        )

    ext, format_label = resolved
    storage_name = storage_filename_for_upload(filename, ext)

    dicom_meta: dict[str, str] = {}
    if ext in {".dcm", ".dicom"} or is_dicom_bytes(content):
        dicom_meta = _extract_dicom_metadata(content)
        ext = ".dcm"
        format_label = "DICOM"

    meta = dict(metadata or {})
    meta.update(
        {
            "original_filename": filename,
            "relative_path": relative_path or filename,
            "file_extension": ext,
            "file_size": len(content),
            "file_size_label": _format_bytes(len(content)),
            "imaging_format": format_label,
            "is_dicom": ext == ".dcm" or bool(dicom_meta),
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
        raw_filename=storage_name,
        raw_content=content,
        metadata=meta,
    )
