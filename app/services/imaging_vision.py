from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.dicom_preview import is_dicom_document, render_dicom_preview_png
from app.services.imaging_catalog import imaging_row_details
from app.services.ollama import OllamaVisionClient
from app.storage.database import Database
from app.storage.documents import DocumentStore

VISION_SLICE_PROMPT = """This is a single CT slice from an oncology case review (chest/abdomen/pelvis study).

Describe what is visible on this slice for a clinical summary:
- Anatomical level and organs shown
- Any masses, lesions, lymphadenopathy, fluid collections, or other focal abnormalities
- Relevant normal findings when helpful for context

Be factual and cautious. Say when findings are uncertain or windowing limits interpretation.
Do not invent measurements or diagnoses not supported by the image.
Write in complete sentences — not coordinates, bounding boxes, or numbers only."""

MOONDREAM_SLICE_PROMPT = """Caption this CT slice for an oncology clinician.

Question: What organs are visible, and are there any obvious masses, fluid collections, or enlarged lymph nodes? If the slice is a scout/localizer/MIP or too low quality to assess, say that plainly.

Answer in at least 2 complete sentences of plain English. Do NOT return coordinates, bounding boxes, numeric arrays, or a single number."""

MOONDREAM_RETRY_PROMPT = """Describe this medical CT image in plain English for a doctor. Write 2-4 sentences about visible anatomy and any obvious abnormality. Do not output coordinates or bracketed number lists."""

VISION_SYSTEM = (
    "You assist oncology case review by describing medical CT slice images. "
    "Use plain clinical language. Do not claim definitive diagnosis. "
    "Never respond with only numbers, coordinates, or arrays."
)

MAX_VISION_SLICES = 10

NON_DIAGNOSTIC_SERIES_KINDS = frozenset(
    {
        "Scout",
        "Axial MIP",
        "Coronal MIP",
        "MIP",
        "Dose report",
        "Administrative",
    }
)

_NUMERIC_GARBAGE = re.compile(
    r"^[\d\s\.\-\[\],]+$|^\[\s*[\d\.,\s]+\s*\]$"
)


def is_moondream_model(model_name: str | None) -> bool:
    return "moondream" in (model_name or "").lower()


def validate_vision_impression(text: str, *, model_name: str | None = None) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("Vision model returned an empty response.")
    if _NUMERIC_GARBAGE.match(cleaned):
        raise ValueError(_vision_quality_error(model_name))
    letters = sum(ch.isalpha() for ch in cleaned)
    if letters < 20 or len(cleaned.split()) < 6:
        raise ValueError(_vision_quality_error(model_name))
    return cleaned


def _vision_quality_error(model_name: str | None) -> str:
    if is_moondream_model(model_name):
        return (
            "Moondream returned coordinates/numbers instead of a clinical description. "
            "It is unreliable on CT slices — especially Scout, MIP, and coronal reformats. "
            "Prefer axial diagnostic series, or set OLLAMA_VISION_MODEL=llama3.2-vision:11b "
            "after updating Ollama on the VM."
        )
    return (
        "Vision model returned unusable output (too short or numeric only). "
        "Try different axial slices or another vision model."
    )


async def _describe_slice_impression(
    client: OllamaVisionClient,
    *,
    image_b64: str,
    prompt: str,
    row: dict[str, Any],
) -> str:
    series_kind = row.get("series_kind") or ""
    if series_kind in NON_DIAGNOSTIC_SERIES_KINDS:
        prompt = (
            f"{prompt}\n\nNote: this slice is tagged as {series_kind}. "
            "If it is a scout/localizer/MIP, say it is not suitable for detailed read."
        )

    impression = await client.describe_image(
        image_b64=image_b64,
        prompt=prompt,
        system=VISION_SYSTEM,
    )
    try:
        return validate_vision_impression(impression, model_name=client.model_name)
    except ValueError:
        if not is_moondream_model(client.model_name):
            raise
        retry = await client.describe_image(
            image_b64=image_b64,
            prompt=MOONDREAM_RETRY_PROMPT,
            system=None,
        )
        return validate_vision_impression(retry, model_name=client.model_name)


async def _vision_client_ready(client: OllamaVisionClient) -> None:
    status = await client.health()
    if not status.get("connected"):
        raise RuntimeError(
            f"Ollama is not reachable at {client.base_url}. Check OLLAMA_BASE_URL and Tailscale."
        )
    if not status.get("model_available"):
        available = ", ".join(status.get("available_models") or []) or "(none)"
        raise RuntimeError(
            f"Vision model {client.model!r} is not on the Ollama server. "
            f"Run: ollama pull {client.model}. Available: {available}"
        )


def _slice_label(row: dict[str, Any]) -> str:
    parts = [
        row.get("series_key") or row.get("title") or "CT slice",
        row.get("anatomy_level") or "",
        f"instance {row['instance_number']}" if row.get("instance_number") else "",
        f"{row['slice_location']} mm" if row.get("slice_location") else "",
    ]
    return " · ".join(part for part in parts if part)


def sample_document_ids(document_ids: list[str], count: int) -> list[str]:
    if not document_ids:
        return []
    limit = max(1, min(count, len(document_ids), MAX_VISION_SLICES))
    if len(document_ids) <= limit:
        return list(document_ids)
    if limit == 1:
        return [document_ids[len(document_ids) // 2]]
    step = (len(document_ids) - 1) / (limit - 1)
    indices = {round(i * step) for i in range(limit)}
    return [document_ids[i] for i in sorted(indices)]


async def analyze_imaging_slices(
    store: DocumentStore,
    db: Database,
    *,
    document_ids: list[str],
    created_by: str | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    if not document_ids:
        raise ValueError("Select at least one imaging slice")

    unique_ids = list(dict.fromkeys(document_ids))
    if len(unique_ids) > MAX_VISION_SLICES:
        raise ValueError(f"At most {MAX_VISION_SLICES} slices per vision run")

    client = OllamaVisionClient()
    await _vision_client_ready(client)

    slice_ranges = None
    sections: list[str] = []
    slice_rows: list[dict[str, Any]] = []
    source_titles: list[str] = []

    for index, doc_id in enumerate(unique_ids, start=1):
        doc = await db.get_document(doc_id)
        if not doc:
            raise ValueError(f"Document not found: {doc_id}")
        if doc.get("source_type") != "imaging":
            raise ValueError(f"Document is not imaging: {doc.get('title') or doc_id}")
        if not is_dicom_document(doc):
            raise ValueError(f"Vision analysis requires DICOM slices: {doc.get('title') or doc_id}")

        file_path = doc.get("file_path")
        if not file_path or not Path(file_path).is_file():
            raise ValueError(f"Imaging file missing on disk: {doc.get('title') or doc_id}")

        if slice_ranges is None:
            from app.services.imaging_catalog import compute_series_slice_ranges

            all_imaging = await db.list_imaging_documents()
            slice_ranges = compute_series_slice_ranges(all_imaging)

        row = imaging_row_details(doc, slice_ranges=slice_ranges)
        slice_rows.append(row)
        source_titles.append(doc.get("title") or doc_id)

        if on_progress:
            await on_progress(
                {
                    "phase": "rendering",
                    "current": index,
                    "total": len(unique_ids),
                    "slice_label": _slice_label(row),
                }
            )

        png_bytes = render_dicom_preview_png(file_path=Path(file_path), max_dimension=768)
        image_b64 = base64.b64encode(png_bytes).decode("ascii")

        if on_progress:
            await on_progress(
                {
                    "phase": "analyzing",
                    "current": index,
                    "total": len(unique_ids),
                    "slice_label": _slice_label(row),
                }
            )

        prompt = (
            (MOONDREAM_SLICE_PROMPT if is_moondream_model(client.model_name) else VISION_SLICE_PROMPT)
            + f"\n\nSlice metadata: {_slice_label(row)}\n"
            f"Window/kernel: {row.get('window_summary') or row.get('convolution_kernel') or 'unknown'}"
        )
        impression = await _describe_slice_impression(
            client,
            image_b64=image_b64,
            prompt=prompt,
            row=row,
        )

        sections.append(
            "\n".join(
                [
                    f"=== SLICE {index}: {_slice_label(row)} ===",
                    f"Source document: {doc.get('title') or doc_id}",
                    "",
                    impression.strip() or "[No description returned]",
                ]
            )
        )

    anatomy_levels = sorted({row.get("anatomy_level") or "" for row in slice_rows if row.get("anatomy_level")})
    anatomy_label = anatomy_levels[0] if len(anatomy_levels) == 1 else "mixed levels"
    if "Mid (abdomen)" in anatomy_levels and len(anatomy_levels) == 1:
        anatomy_label = "abdomen"
    elif "Superior (chest)" in anatomy_levels and len(anatomy_levels) == 1:
        anatomy_label = "chest"
    elif "Inferior (pelvis)" in anatomy_levels and len(anatomy_levels) == 1:
        anatomy_label = "pelvis"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = f"Vision read — CT {anatomy_label} ({len(unique_ids)} slice{'s' if len(unique_ids) != 1 else ''})"
    body = "\n\n".join(
        [
            "[AI vision read of selected CT DICOM slices — for oncology case review]",
            f"Model: {client.model_name}",
            f"Generated: {timestamp}",
            f"Slices analyzed: {len(unique_ids)}",
            "",
            "These impressions were generated from pixel data sent to a local vision model.",
            "Use alongside the official radiology report; this is not a substitute for formal read.",
            "Tip: select axial diagnostic slices — Scout, MIP, and coronal reformats often produce poor AI reads.",
            "",
            *sections,
        ]
    )

    created = await store.create_document(
        title=title,
        source_type="text",
        extracted_text=body,
        metadata={
            "vision_read": True,
            "vision_model": client.model_name,
            "vision_source_slice_ids": unique_ids,
            "vision_source_titles": source_titles,
            "vision_anatomy_levels": anatomy_levels,
            "created_by": created_by,
        },
    )

    return {
        "document_id": created["id"],
        "title": created["title"],
        "slice_count": len(unique_ids),
        "source_slice_ids": unique_ids,
        "model": client.model_name,
        "text_preview": body[:500],
    }
