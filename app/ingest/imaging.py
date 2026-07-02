from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.storage.documents import DocumentStore

if TYPE_CHECKING:
    from app.storage.database import Database

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
        if isinstance(value, (list, tuple)):
            return "\\".join(str(v).strip() for v in value if str(v).strip())
        return str(value).strip()

    image_position = getattr(dataset, "ImagePositionPatient", None)
    image_position_z = ""
    if image_position is not None and len(image_position) >= 3:
        image_position_z = str(image_position[2]).strip()

    fields = {
        "modality": _get("Modality"),
        "study_date": _get("StudyDate"),
        "study_description": _get("StudyDescription"),
        "series_description": _get("SeriesDescription"),
        "series_number": _get("SeriesNumber"),
        "instance_number": _get("InstanceNumber"),
        "body_part": _get("BodyPartExamined"),
        "slice_thickness": _get("SliceThickness"),
        "slice_location": _get("SliceLocation"),
        "image_position_z": image_position_z,
        "spacing_between_slices": _get("SpacingBetweenSlices"),
        "window_center": _get("WindowCenter"),
        "window_width": _get("WindowWidth"),
        "convolution_kernel": _get("ConvolutionKernel"),
        "protocol_name": _get("ProtocolName"),
        "image_type": _get("ImageType"),
        "rows": _get("Rows"),
        "columns": _get("Columns"),
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
        ("Slice location (mm)", "slice_location"),
        ("Image Z position (mm)", "image_position_z"),
        ("Reconstruction kernel", "convolution_kernel"),
        ("Window center/width", "window_center"),
        ("Window width", "window_width"),
        ("Protocol", "protocol_name"),
        ("Slice spacing (mm)", "spacing_between_slices"),
        ("Image type", "image_type"),
    ):
        value = dicom_meta.get(key)
        if value:
            if key == "window_center" and dicom_meta.get("window_width"):
                lines.append(f"Window preset: {value} / {dicom_meta['window_width']}")
            elif key == "window_width" and dicom_meta.get("window_center"):
                continue
            else:
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


def merge_dicom_metadata(meta: dict[str, Any], dicom_meta: dict[str, str]) -> dict[str, Any]:
    merged = dict(meta or {})
    merged.update({f"dicom_{key}": value for key, value in dicom_meta.items()})
    if dicom_meta.get("modality"):
        merged["modality"] = dicom_meta["modality"]
    return merged


async def reindex_imaging_document(
    store: DocumentStore,
    db: "Database",
    doc: dict[str, Any],
) -> bool:
    from pathlib import Path

    path_value = doc.get("file_path")
    if not path_value:
        return False
    path = Path(path_value)
    if not path.is_file():
        return False

    content = path.read_bytes()
    resolved = resolve_imaging_type(
        (doc.get("metadata") or {}).get("original_filename") or path.name,
        content,
    )
    if not resolved:
        return False

    ext, _format_label = resolved
    dicom_meta: dict[str, str] = {}
    if ext in {".dcm", ".dicom"} or is_dicom_bytes(content):
        dicom_meta = _extract_dicom_metadata(content)

    meta = merge_dicom_metadata(doc.get("metadata") or {}, dicom_meta)
    filename = meta.get("original_filename") or path.name
    extracted = _build_extracted_text(
        filename=filename,
        ext=meta.get("file_extension") or ext,
        file_size=meta.get("file_size") or len(content),
        relative_path=meta.get("relative_path"),
        notes=None,
        dicom_meta=dicom_meta,
    )
    saved_text = await store.save_extracted_text(doc["id"], extracted)
    await db.update_document_metadata(
        doc["id"],
        metadata=meta,
        extracted_path=str(saved_text),
    )
    return True


async def reindex_all_imaging_metadata(store: DocumentStore, db: "Database") -> dict[str, int]:
    documents = await db.list_imaging_documents()
    updated = 0
    skipped = 0
    for doc in documents:
        try:
            if await reindex_imaging_document(store, db, doc):
                updated += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    return {"total": len(documents), "updated": updated, "skipped": skipped}
