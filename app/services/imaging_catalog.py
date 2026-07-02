from __future__ import annotations

from typing import Any

FACET_LABELS: dict[str, str] = {
    "modality": "Modality",
    "study_description": "Study",
    "study_date": "Study date",
    "series_key": "Series",
    "series_kind": "Series type",
    "series_description": "Series name",
    "convolution_kernel": "Reconstruction kernel",
    "anatomy_level": "Anatomical level",
    "body_part": "Body part (DICOM tag)",
}

SERIES_KIND_ORDER = (
    "Lung window",
    "Axial",
    "Coronal",
    "Sagittal",
    "Axial MIP",
    "Coronal MIP",
    "MIP",
    "Scout",
    "Administrative",
    "Dose report",
    "Unknown",
)

ANATOMY_LEVEL_ORDER = (
    "Superior (chest)",
    "Mid (abdomen)",
    "Inferior (pelvis)",
)


def _meta(meta: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _float_or_none(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def infer_series_kind(meta: dict[str, Any]) -> str:
    desc = _meta(meta, "dicom_series_description", "series_description")
    lower = desc.lower()
    if not desc:
        return "Unknown"
    if "lung" in lower:
        return "Lung window"
    if "scout" in lower:
        return "Scout"
    if "dose report" in lower or lower == "dose report":
        return "Dose report"
    if any(token in lower for token in ("paper", "referral", "administration", "erequest")):
        return "Administrative"
    if "mip" in lower:
        if "coronal" in lower:
            return "Coronal MIP"
        if "axial" in lower:
            return "Axial MIP"
        return "MIP"
    if "coronal" in lower:
        return "Coronal"
    if "sagittal" in lower:
        return "Sagittal"
    if "axial" in lower:
        return "Axial"
    return desc


def format_series_key(meta: dict[str, Any]) -> str:
    number = _meta(meta, "dicom_series_number", "series_number")
    desc = _meta(meta, "dicom_series_description", "series_description")
    if number and desc:
        return f"{number} · {desc}"
    return desc or number


def format_window_summary(meta: dict[str, Any]) -> str:
    kernel = _meta(meta, "dicom_convolution_kernel", "convolution_kernel")
    center = _meta(meta, "dicom_window_center", "window_center")
    width = _meta(meta, "dicom_window_width", "window_width")
    if kernel and kernel.upper() == "LUNG":
        return "Lung kernel"
    if center and width:
        return f"{center} / {width}"
    if kernel:
        return kernel
    return ""


def compute_series_slice_ranges(documents: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    ranges: dict[str, list[float]] = {}
    for doc in documents:
        meta = doc.get("metadata") or {}
        series_key = format_series_key(meta)
        if not series_key:
            continue
        loc = _float_or_none(_meta(meta, "dicom_slice_location", "slice_location"))
        if loc is None:
            loc = _float_or_none(_meta(meta, "dicom_image_position_z", "image_position_z"))
        if loc is None:
            continue
        ranges.setdefault(series_key, []).append(loc)

    return {
        key: (min(values), max(values))
        for key, values in ranges.items()
        if values
    }


def infer_anatomy_level(meta: dict[str, Any], slice_ranges: dict[str, tuple[float, float]]) -> str:
    series_key = format_series_key(meta)
    bounds = slice_ranges.get(series_key)
    loc = _float_or_none(_meta(meta, "dicom_slice_location", "slice_location"))
    if loc is None:
        loc = _float_or_none(_meta(meta, "dicom_image_position_z", "image_position_z"))
    if loc is None or not bounds:
        return ""
    lo, hi = bounds
    if hi == lo:
        return "Mid (abdomen)"
    pct = (loc - lo) / (hi - lo)
    if pct >= 0.66:
        return "Superior (chest)"
    if pct >= 0.33:
        return "Mid (abdomen)"
    return "Inferior (pelvis)"


def imaging_filter_fields(
    meta: dict[str, Any],
    *,
    slice_ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, str]:
    meta = meta or {}
    ranges = slice_ranges or {}
    return {
        "modality": _meta(meta, "modality", "dicom_modality"),
        "study_description": _meta(meta, "dicom_study_description", "study_description"),
        "study_date": _meta(meta, "dicom_study_date", "study_date"),
        "series_description": _meta(meta, "dicom_series_description", "series_description"),
        "series_number": _meta(meta, "dicom_series_number", "series_number"),
        "series_key": format_series_key(meta),
        "body_part": _meta(meta, "dicom_body_part", "body_part"),
        "series_kind": infer_series_kind(meta),
        "convolution_kernel": _meta(meta, "dicom_convolution_kernel", "convolution_kernel"),
        "anatomy_level": infer_anatomy_level(meta, ranges),
    }


def imaging_row_details(
    doc: dict[str, Any],
    *,
    slice_ranges: dict[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    meta = doc.get("metadata") or {}
    fields = imaging_filter_fields(meta, slice_ranges=slice_ranges)
    return {
        "id": doc["id"],
        "title": doc.get("title") or "",
        "series_key": fields.get("series_key") or "",
        "series_kind": fields.get("series_kind") or "",
        "series_description": fields.get("series_description") or "",
        "series_number": fields.get("series_number") or "",
        "instance_number": _meta(meta, "dicom_instance_number", "instance_number"),
        "slice_location": _meta(meta, "dicom_slice_location", "slice_location"),
        "image_position_z": _meta(meta, "dicom_image_position_z", "image_position_z"),
        "slice_thickness": _meta(meta, "dicom_slice_thickness", "slice_thickness"),
        "convolution_kernel": fields.get("convolution_kernel") or "",
        "window_summary": format_window_summary(meta),
        "protocol_name": _meta(meta, "dicom_protocol_name", "protocol_name"),
        "anatomy_level": fields.get("anatomy_level") or "",
        "study_description": fields.get("study_description") or "",
        "preview_url": f"/api/documents/{doc['id']}/preview",
    }


def build_imaging_facets(documents: list[dict[str, Any]]) -> dict[str, Any]:
    slice_ranges = compute_series_slice_ranges(documents)
    facet_counts: dict[str, dict[str, int]] = {key: {} for key in FACET_LABELS}
    total = 0
    enriched = 0

    for doc in documents:
        meta = doc.get("metadata") or {}
        if _meta(meta, "dicom_slice_location", "slice_location") or _meta(
            meta, "dicom_convolution_kernel", "convolution_kernel"
        ):
            enriched += 1
        fields = imaging_filter_fields(meta, slice_ranges=slice_ranges)
        total += 1
        for key, value in fields.items():
            if key not in facet_counts:
                continue
            if not value or value == "Unknown":
                continue
            bucket = facet_counts[key]
            bucket[value] = bucket.get(value, 0) + 1

    facets: list[dict[str, Any]] = []
    for key, label in FACET_LABELS.items():
        counts = facet_counts[key]
        if not counts:
            continue
        values = sorted(
            counts.items(),
            key=lambda item: (
                SERIES_KIND_ORDER.index(item[0])
                if key == "series_kind" and item[0] in SERIES_KIND_ORDER
                else ANATOMY_LEVEL_ORDER.index(item[0])
                if key == "anatomy_level" and item[0] in ANATOMY_LEVEL_ORDER
                else -counts[item[0]],
                item[0].lower(),
            ),
        )
        facets.append(
            {
                "key": key,
                "label": label,
                "values": [{"value": value, "count": count} for value, count in values],
            }
        )

    return {
        "total": total,
        "metadata_enriched": enriched,
        "needs_reindex": enriched < total,
        "facets": facets,
    }


def build_imaging_series_catalog(documents: list[dict[str, Any]]) -> dict[str, Any]:
    slice_ranges = compute_series_slice_ranges(documents)
    groups: dict[str, dict[str, Any]] = {}

    for doc in documents:
        row = imaging_row_details(doc, slice_ranges=slice_ranges)
        key = row["series_key"] or row["series_description"] or doc["id"]
        group = groups.get(key)
        if not group:
            groups[key] = {
                "series_key": key,
                "series_number": row["series_number"],
                "series_description": row["series_description"],
                "series_kind": row["series_kind"],
                "count": 0,
                "instance_numbers": [],
                "slice_locations": [],
                "convolution_kernels": set(),
                "window_summaries": set(),
                "protocol_name": row["protocol_name"],
                "sample_document_id": doc["id"],
                "preview_url": row["preview_url"],
            }
            group = groups[key]
        group["count"] += 1
        if row["instance_number"]:
            group["instance_numbers"].append(int(float(row["instance_number"])))
        loc = _float_or_none(row["slice_location"]) or _float_or_none(row["image_position_z"])
        if loc is not None:
            group["slice_locations"].append(loc)
        if row["convolution_kernel"]:
            group["convolution_kernels"].add(row["convolution_kernel"])
        if row["window_summary"]:
            group["window_summaries"].add(row["window_summary"])

    series_list: list[dict[str, Any]] = []
    for group in groups.values():
        inst = group.pop("instance_numbers")
        locs = group.pop("slice_locations")
        kernels = sorted(group.pop("convolution_kernels"))
        windows = sorted(group.pop("window_summaries"))
        group["instance_min"] = min(inst) if inst else None
        group["instance_max"] = max(inst) if inst else None
        group["slice_location_min"] = round(min(locs), 1) if locs else None
        group["slice_location_max"] = round(max(locs), 1) if locs else None
        group["convolution_kernels"] = kernels
        group["window_summaries"] = windows
        group["window_summary"] = windows[0] if len(windows) == 1 else ", ".join(windows[:2])
        series_list.append(group)

    series_list.sort(
        key=lambda item: (
            SERIES_KIND_ORDER.index(item["series_kind"])
            if item["series_kind"] in SERIES_KIND_ORDER
            else 999,
            item["series_number"] or "",
            item["series_description"] or "",
        )
    )
    return {"series": series_list, "total_series": len(series_list)}


def normalize_filters(raw: dict[str, str | None]) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key in FACET_LABELS:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            cleaned[key] = text
    return cleaned


def document_matches_filters(
    meta: dict[str, Any],
    filters: dict[str, str],
    *,
    slice_ranges: dict[str, tuple[float, float]] | None = None,
) -> bool:
    if not filters:
        return True
    fields = imaging_filter_fields(meta, slice_ranges=slice_ranges)
    for key, expected in filters.items():
        if fields.get(key) != expected:
            return False
    return True


def match_imaging_documents(
    documents: list[dict[str, Any]],
    *,
    filters: dict[str, str | None],
    preview_limit: int = 40,
) -> dict[str, Any]:
    slice_ranges = compute_series_slice_ranges(documents)
    normalized = normalize_filters(filters)
    matched: list[dict[str, Any]] = []

    for doc in documents:
        if document_matches_filters(
            doc.get("metadata") or {},
            normalized,
            slice_ranges=slice_ranges,
        ):
            matched.append(imaging_row_details(doc, slice_ranges=slice_ranges))

    matched.sort(
        key=lambda row: (
            row.get("series_key") or "",
            float(row["instance_number"]) if row.get("instance_number") else 0,
            row.get("title") or "",
        )
    )
    limit = max(1, min(preview_limit, 100))
    return {
        "filters": normalized,
        "total": len(matched),
        "document_ids": [row["id"] for row in matched],
        "preview": matched[:limit],
    }
