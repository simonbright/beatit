from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

DICOM_EXTENSIONS = {".dcm", ".dicom"}

DICOM_VIEW_FIELDS: tuple[tuple[str, str], ...] = (
    ("modality", "Modality"),
    ("dicom_study_date", "Study date"),
    ("dicom_study_description", "Study description"),
    ("dicom_series_description", "Series description"),
    ("dicom_series_number", "Series number"),
    ("dicom_instance_number", "Instance number"),
    ("dicom_body_part", "Body part examined"),
    ("dicom_slice_thickness", "Slice thickness"),
    ("imaging_format", "Format"),
    ("file_size_label", "File size"),
    ("original_filename", "Filename"),
)


def is_dicom_extension(ext: str) -> bool:
    normalized = (ext or "").lower()
    if normalized and not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized in DICOM_EXTENSIONS


def is_dicom_document(doc: dict[str, Any], ext: str | None = None) -> bool:
    meta = doc.get("metadata") or {}
    if meta.get("is_dicom") or meta.get("imaging_format") == "DICOM":
        return True
    if ext is None:
        ext = meta.get("file_extension") or Path(doc.get("file_path") or "").suffix
    if is_dicom_extension(str(ext)):
        return True
    file_path = doc.get("file_path")
    if file_path and Path(file_path).is_file():
        try:
            head = Path(file_path).read_bytes()[:132]
            if len(head) >= 132 and head[128:132] == b"DICM":
                return True
        except OSError:
            pass
    return False


def _format_study_date(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[4:6]}/{raw[6:8]}/{raw[0:4]}"
    return raw


def dicom_view_metadata(doc: dict[str, Any]) -> list[dict[str, str]]:
    meta = doc.get("metadata") or {}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for key, label in DICOM_VIEW_FIELDS:
        value = str(meta.get(key) or "").strip()
        if not value or key in seen:
            continue
        if key == "dicom_study_date":
            value = _format_study_date(value)
        rows.append({"label": label, "value": value})
        seen.add(key)

    return rows


def _normalize_to_uint8(arr: Any) -> Any:
    import numpy as np

    data = arr.astype(np.float64)
    if data.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)

    lo, hi = np.percentile(data, (1, 99))
    if hi <= lo:
        lo, hi = float(data.min()), float(data.max())
    if hi <= lo:
        return np.zeros(arr.shape, dtype=np.uint8)

    scaled = (data - lo) / (hi - lo) * 255.0
    return np.clip(scaled, 0, 255).astype(np.uint8)


def render_dicom_preview_png(
    *,
    file_path: Path | None = None,
    content: bytes | None = None,
    max_dimension: int | None = None,
) -> bytes:
    import numpy as np
    import pydicom
    from PIL import Image

    if file_path is not None:
        dataset = pydicom.dcmread(str(file_path), force=True)
    elif content is not None:
        dataset = pydicom.dcmread(BytesIO(content), force=True)
    else:
        raise ValueError("file_path or content is required")

    pixel_array = dataset.pixel_array

    try:
        from pydicom.pixels import apply_modality_lut, apply_voi_lut

        pixel_array = apply_modality_lut(pixel_array, dataset)
        pixel_array = apply_voi_lut(pixel_array, dataset)
    except Exception:
        pass

    if getattr(dataset, "PhotometricInterpretation", "") == "MONOCHROME1":
        pixel_array = np.max(pixel_array) - pixel_array

    if pixel_array.ndim == 2:
        image_data = _normalize_to_uint8(pixel_array)
        image = Image.fromarray(image_data, mode="L")
    elif pixel_array.ndim == 3 and pixel_array.shape[2] in (3, 4):
        image_data = _normalize_to_uint8(pixel_array[:, :, :3])
        image = Image.fromarray(image_data, mode="RGB")
    else:
        raise ValueError("Unsupported DICOM pixel layout for preview")

    buffer = BytesIO()
    if max_dimension and max(image.size) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), resample=Image.Resampling.LANCZOS)
    image.save(buffer, format="PNG")
    return buffer.getvalue()
