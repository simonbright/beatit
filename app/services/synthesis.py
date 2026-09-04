from typing import Any

from app.services.assessment_parse import ensure_executive_summary, open_items_to_json, parse_assessment
from app.services.llm import LLMClient
from app.services.content_policy import PALLIATIVE_EXCLUSION, filter_palliative_content
from app.services.chat_observations import (
    format_chat_observations_for_prompt,
    observation_library_document_ids,
    resolve_observations_for_analysis,
)
from app.services.source_policy import (
    BASELINE_GAP_RULES,
    BASELINE_GUIDANCE_SECTION,
    COMPREHENSIVE_SYNTHESIS_RULES,
    CUSTOM_QUERY_RESPONSE_STRUCTURE,
    LIST_ITEM_SOURCE_RULES,
    SOURCE_ATTRIBUTION_RULES,
    TRIAL_SEARCH_QUERY_INSTRUCTIONS,
    infer_assessment_specialty,
    response_structure_with_sources,
)
from app.services.source_normalize import enrich_with_sources
from app.storage.database import Database
from app.storage.documents import DocumentStore
from app.services.patient_context import DEFAULT_PATIENT_CONTEXT, DEFAULT_REVIEWER_CONTEXT
from app.services.case_manager import format_profile_for_prompt, get_active_context

MEDICAL_SYSTEM_TEMPLATE = """{reviewer_context}

Patient demographics and vitals:
{patient_demographics}

Patient and case context (baseline — update as new evidence arrives):
{patient_context}

Core responsibilities:
1. Synthesize available clinical and research material clearly and accurately across ALL sources in scope
2. Distinguish what is KNOWN vs UNKNOWN vs UNCERTAIN
3. Identify critical information gaps that should be closed before major treatment decisions
4. Outline a broad range of treatment options (surgery, systemic therapy, radiation, clinical trials, molecular testing, staging workup)
5. Note where evidence is strong vs where decisions depend on patient-specific factors (performance status, comorbidities, goals of care, biomarkers)

Important constraints:
- Do not discuss, recommend, or mention palliative care — see PALLIATIVE EXCLUSION below
- This is decision-support for clinicians and informed family discussion — {care_constraint}
- Match specialty language to the active case (e.g. cardiology vs oncology); do not default to oncology wording
- Do not invent lab values, imaging findings, or staging that are not in the provided documents
- When data is missing, say so explicitly and explain why it matters
- Use clear headings and bullet points
- Flag urgent red-flag symptoms or scenarios when relevant
- Be compassionate but clinically precise"""


async def build_medical_system_prompt(db: Database) -> str:
    reviewer = await db.get_setting("reviewer_context") or DEFAULT_REVIEWER_CONTEXT
    patient = await db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
    ctx = get_active_context()
    demographics = format_profile_for_prompt(ctx.get("patient_id"), ctx.get("patient_label"))
    if not demographics.strip():
        demographics = "- Not set yet."
    specialty = infer_assessment_specialty(
        case_label=ctx.get("case_label"),
        patient_context=patient,
    )
    base = MEDICAL_SYSTEM_TEMPLATE.format(
        reviewer_context=reviewer.strip(),
        patient_demographics=demographics.strip(),
        patient_context=patient.strip(),
        care_constraint=specialty["care_constraint"],
    )
    return f"{base}\n\n{PALLIATIVE_EXCLUSION}"


IMAGING_STUB_MARKER = "Imaging pixels are stored locally and are not sent to the LLM"


def _is_imaging_stub(item: dict[str, Any]) -> bool:
    if item.get("source_type") != "imaging":
        return False
    meta = item.get("metadata") or {}
    if meta.get("vision_read"):
        return False
    return IMAGING_STUB_MARKER in (item.get("text") or "")


def _parse_stub_field(text: str, label: str) -> str:
    prefix = f"{label}:"
    for line in (text or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _aggregate_imaging_stubs(stubs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in stubs:
        meta = item.get("metadata") or {}
        rel = str(meta.get("relative_path") or item.get("title") or "").replace("\\", "/")
        folder = rel.split("/")[0] if "/" in rel else "Imaging upload"
        text = item.get("text") or ""
        modality = (
            _parse_stub_field(text, "Modality")
            or meta.get("modality")
            or meta.get("dicom_modality")
            or "Imaging"
        )
        study_date = _parse_stub_field(text, "Study date") or meta.get("dicom_study_date") or ""
        study_desc = (
            _parse_stub_field(text, "Study")
            or meta.get("dicom_study_description")
            or ""
        )
        key = f"{folder}|{modality}|{study_date}|{study_desc}"
        if key not in groups:
            groups[key] = {
                "folder": folder,
                "modality": modality,
                "study_date": study_date,
                "study_desc": study_desc,
                "count": 0,
                "sample_titles": [],
            }
        group = groups[key]
        group["count"] += 1
        if len(group["sample_titles"]) < 3 and item.get("title"):
            group["sample_titles"].append(item["title"])

    aggregated: list[dict[str, Any]] = []
    for key, group in groups.items():
        date_part = group["study_date"] or "unknown date"
        title = f'{group["folder"]} — {group["modality"]} — {date_part} ({group["count"]} DICOM slices)'
        body_lines = [
            f"[Aggregated DICOM upload — {group['count']} slice files; pixel data not included in LLM context]",
            f"Upload folder: {group['folder']}",
            f"Modality: {group['modality']}",
        ]
        if group["study_date"]:
            body_lines.append(f"Study date: {group['study_date']}")
        if group["study_desc"]:
            body_lines.append(f"Study description: {group['study_desc']}")
        if group["sample_titles"]:
            body_lines.append("Sample slice titles: " + "; ".join(group["sample_titles"]))
        body_lines.append(
            "Use formal radiology reports, vision reads, and clinical notes for imaging interpretation — not individual slice metadata."
        )
        aggregated.append(
            {
                "id": f"imaging-group-{abs(hash(key)) % 10**8}",
                "title": title,
                "source_type": "imaging",
                "source_uri": None,
                "text": "\n".join(body_lines),
                "metadata": {},
            }
        )
    return sorted(aggregated, key=lambda row: row["title"])


def _corpus_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    source_type = item.get("source_type") or ""
    title_lower = (item.get("title") or "").lower()
    priority = {
        "pdf": 0,
        "text": 1,
        "youtube": 2,
        "facebook": 3,
        "url": 4,
        "video": 5,
        "imaging": 8,
    }.get(source_type, 6)
    if "vision read" in title_lower:
        priority = 0
    return (priority, -(len(item.get("text") or "")), item.get("title") or "")


def _prepare_corpus_items(corpus: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    substantive: list[dict[str, Any]] = []
    stubs: list[dict[str, Any]] = []
    for item in corpus:
        if _is_imaging_stub(item):
            stubs.append(item)
        else:
            substantive.append(item)

    substantive.sort(key=_corpus_sort_key)
    aggregated = _aggregate_imaging_stubs(stubs)
    stats = {
        "total_docs": len(corpus),
        "substantive_count": len(substantive),
        "imaging_stub_count": len(stubs),
        "imaging_groups": len(aggregated),
        "included_titles": [],
        "truncated_titles": [],
    }
    return substantive + aggregated, stats


def _format_document_inventory(corpus: list[dict[str, Any]]) -> str:
    rows = sorted(
        corpus,
        key=lambda item: (
            (item.get("case_label") or ""),
            (item.get("source_type") or ""),
            (item.get("title") or ""),
        ),
    )
    lines = []
    for item in rows:
        if not item.get("title"):
            continue
        case = item.get("case_label")
        case_bit = f" ({case})" if case else ""
        lines.append(
            f'- [{item.get("source_type", "unknown").upper()}] "{item.get("title")}"{case_bit}'
        )
    return "\n".join(lines) if lines else "[No documents in scope]"


def _format_coverage_notes(stats: dict[str, Any]) -> str:
    lines: list[str] = []
    if stats.get("imaging_stub_count"):
        lines.append(
            f"- {stats['imaging_stub_count']} DICOM slice file(s) summarized into "
            f"{stats.get('imaging_groups', 0)} upload group(s). Pixel data is excluded; use radiology reports and vision reads."
        )
    truncated = stats.get("truncated_titles") or []
    if truncated:
        preview = ", ".join(f'"{title}"' for title in truncated[:12])
        suffix = f" (+{len(truncated) - 12} more)" if len(truncated) > 12 else ""
        lines.append(
            f"- {len(truncated)} document(s) were partially truncated due to context size: {preview}{suffix}"
        )
    return "\n".join(lines)


def _format_corpus(corpus: list[dict[str, Any]], max_chars: int = 200_000) -> tuple[str, str]:
    items, stats = _prepare_corpus_items(corpus)
    sections: list[str] = []
    used = 0

    for item in items:
        case_label = item.get("case_label") or (item.get("metadata") or {}).get("case_label")
        case_line = f"Focus: {case_label}\n" if case_label else ""
        header = (
            f"### [{item['source_type'].upper()}] {item['title']}\n"
            f"ID: {item['id']}\n"
            f"{case_line}"
            f"Source: {item.get('source_uri') or 'local'}\n"
        )
        meta = item.get("metadata") or {}
        report_kind = meta.get("clinical_report_kind") if isinstance(meta, dict) else None
        if report_kind and str(report_kind).lower() not in ("", "unknown"):
            label = meta.get("clinical_report_kind_label") or report_kind
            header += (
                f"Clinical report type: {label} — treat as diagnostic evidence "
                f"(lab/imaging/pathology report), not a generic note.\n"
            )
        body = item["text"]
        chunk = f"{header}\n{body}\n"
        if used + len(chunk) > max_chars:
            remaining = max_chars - used - len(header) - 50
            if remaining > 500:
                sections.append(f"{header}\n{body[:remaining]}\n...[truncated]\n")
                stats["truncated_titles"].append(item["title"])
            else:
                stats["truncated_titles"].append(item["title"])
            break
        sections.append(chunk)
        used += len(chunk)
        stats["included_titles"].append(item["title"])

    text = "\n---\n".join(sections) if sections else "[No document text available]"
    return text, _format_coverage_notes(stats)


def _is_trial_search_query(query: str) -> bool:
    lower = query.lower()
    keywords = (
        "clinical trial",
        "clinical trials",
        "trial",
        "trials",
        "therapeutic",
        "therapeutics",
        "treatment option",
        "study",
        "studies",
        "nct",
        "investigational",
    )
    return any(keyword in lower for keyword in keywords)


def _response_structure_for_analysis(
    *,
    analysis_type: str,
    query: str,
    specialty: dict[str, str] | None = None,
) -> str:
    if analysis_type == "query":
        structure = f"{CUSTOM_QUERY_RESPONSE_STRUCTURE}\n\n{LIST_ITEM_SOURCE_RULES}"
        if _is_trial_search_query(query):
            structure = f"{structure}\n\n{TRIAL_SEARCH_QUERY_INSTRUCTIONS}"
        return structure
    return f"{response_structure_with_sources(specialty)}\n\n{COMPREHENSIVE_SYNTHESIS_RULES}"


BASELINE_BUILD_ON_PRIOR_SECTION = """
=== PRIOR BASELINE ASSESSMENT (revise and build on this — do not discard accurate content) ===
{prior_text}
{new_docs_note}
=== INSTRUCTIONS FOR THIS RE-RUN ===
This is an UPDATE to the prior baseline, not a blank-slate rewrite.
- RETAIN findings from the prior assessment that remain supported by the documents below.
- INTEGRATE new documents (especially vision reads and radiology reports) into the appropriate sections.
- UPDATE open items: resolve items now documented; add new gaps only when truly missing from all sources.
- Do NOT drop prior clinical facts unless contradicted by stronger source evidence in the current document set.
"""


def _prior_assessment_text(analysis: dict[str, Any]) -> str:
    summary = (analysis.get("executive_summary") or "").strip()
    response = (analysis.get("response") or "").strip()
    if summary and response and response not in summary:
        return f"{summary}\n\n{response}"
    return summary or response


def _new_document_titles(
    prior_ids: list[str],
    corpus: list[dict[str, Any]],
) -> list[str]:
    prior = set(prior_ids or [])
    return [d["title"] for d in corpus if d.get("id") not in prior and d.get("title")]


class SynthesisService:
    def __init__(
        self,
        store: DocumentStore,
        db: Database,
        llm: LLMClient | None = None,
    ):
        self.store = store
        self.db = db
        self.llm = llm or LLMClient()

    async def analyze(
        self,
        *,
        query: str,
        document_ids: list[str] | None = None,
        include_baseline_assessment: bool = False,
        assessment_guidance: str | None = None,
        analysis_type: str = "query",
        created_by: str | None = None,
        build_on_analysis_id: str | None = None,
        chat_observation_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        observations = await resolve_observations_for_analysis(
            self.db,
            observation_ids=chat_observation_ids,
        )
        obs_doc_ids = await observation_library_document_ids(observations)
        merged_doc_ids = document_ids
        if obs_doc_ids:
            if merged_doc_ids is None:
                pass
            else:
                merged = list(merged_doc_ids)
                seen = set(merged)
                for doc_id in obs_doc_ids:
                    if doc_id not in seen:
                        merged.append(doc_id)
                        seen.add(doc_id)
                merged_doc_ids = merged

        corpus = await self.store.get_corpus(merged_doc_ids)
        corpus_text, coverage_notes = _format_corpus(corpus)
        doc_titles = [d["title"] for d in corpus if d.get("title")]
        for obs in observations:
            obs_title = (obs.get("title") or "").strip()
            if obs_title and obs_title not in doc_titles:
                doc_titles.append(obs_title)
        title_list = "\n".join(f'- "{t}"' for t in doc_titles) if doc_titles else "[No documents stored]"
        inventory = _format_document_inventory(corpus)
        coverage_section = (
            f"\n=== CORPUS COVERAGE NOTES ===\n{coverage_notes}\n"
            if coverage_notes
            else ""
        )
        inventory_section = f"""
=== DOCUMENT INVENTORY ({len(corpus)} documents in this assessment scope) ===
{inventory}
"""

        prior_section = ""
        if build_on_analysis_id:
            prior = await self.db.get_analysis_by_id(build_on_analysis_id)
            if prior and prior.get("record_status") == "official":
                prior_text = _prior_assessment_text(prior)
                new_titles = _new_document_titles(prior.get("document_ids") or [], corpus)
                new_docs_note = ""
                if new_titles:
                    listed = "\n".join(f'- "{t}"' for t in new_titles)
                    new_docs_note = (
                        f"\n=== NEW DOCUMENTS SINCE PRIOR BASELINE (integrate these) ===\n{listed}\n"
                    )
                if prior_text:
                    prior_section = BASELINE_BUILD_ON_PRIOR_SECTION.format(
                        prior_text=prior_text[:120000],
                        new_docs_note=new_docs_note,
                    )

        ctx = get_active_context()
        patient_setting = await self.db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
        specialty = infer_assessment_specialty(
            case_label=ctx.get("case_label"),
            patient_context=patient_setting,
        )

        if include_baseline_assessment and not query.strip():
            analysis_type = "baseline"
            query = specialty["baseline_query"]

        gap_rules = f"\n{BASELINE_GAP_RULES}\n" if analysis_type == "baseline" else ""
        guidance_text = (assessment_guidance or "").strip()
        guidance_section = (
            BASELINE_GUIDANCE_SECTION.format(guidance=guidance_text)
            if analysis_type == "baseline" and guidance_text
            else ""
        )

        chat_section = format_chat_observations_for_prompt(observations)
        if chat_section:
            chat_section = f"\n{chat_section}\n"
        obs_titles = [o["title"] for o in observations if o.get("title")]
        chat_title_lines = (
            "\n".join(f'- Chat observation "{t}"' for t in obs_titles)
            if obs_titles
            else ""
        )
        chat_titles_block = (
            f"\nCHAT OBSERVATION TITLES — use EXACT strings inside [SOURCE: Chat observation \"…\"] tags:\n"
            f"{chat_title_lines}\n"
            if chat_title_lines
            else ""
        )

        case_focus = (ctx.get("case_label") or "").strip()
        case_focus_line = (
            f"\nActive case focus: {case_focus}. Use {specialty['care_team']} wording — "
            f"do not invent an oncology framing unless this case is oncology.\n"
            if case_focus
            else f"\nUse {specialty['care_team']} wording for clinician questions — "
            f"do not default to oncology.\n"
        )

        prompt = f"""Use the following stored research and clinical material as your evidence base.
If the documents do not contain information needed to answer, state the gap explicitly.
{inventory_section}{coverage_section}
DOCUMENT TITLES — use these EXACT strings inside [SOURCE: Document "..."] tags:
{title_list}
{chat_titles_block}
=== STORED DOCUMENTS ===
{corpus_text}
{chat_section}{prior_section}{guidance_section}
=== USER QUERY (answer this directly — this is the primary task) ===
{query}
{case_focus_line}{gap_rules}
{_response_structure_for_analysis(analysis_type=analysis_type, query=query, specialty=specialty)}

CRITICAL: Every factual bullet MUST end with [SOURCE: Document "..."] or another SOURCE tag.
Do NOT write parenthetical citations like (CT Report). Use [SOURCE: Document "exact title"] only.
Do NOT mention palliative care, hospice, or comfort care anywhere in the response.
Do NOT substitute a generic case summary when the user asked for a specific deliverable (e.g. a trial list).

Use clear ### headings for each section."""

        system = await build_medical_system_prompt(self.db)
        system = f"{system}\n\n{SOURCE_ATTRIBUTION_RULES}"

        response = await self.llm.generate(
            prompt=prompt,
            system=system,
            temperature=0.2,
        )

        response = filter_palliative_content(response)

        response, _attribution_level = enrich_with_sources(
            response, doc_titles, annotate_staging=True
        )
        parsed = parse_assessment(response)
        parsed["executive_summary"] = ensure_executive_summary(parsed, response)
        executive_summary, _ = enrich_with_sources(
            parsed["executive_summary"], doc_titles, annotate_staging=False
        )
        parsed["executive_summary"] = filter_palliative_content(
            executive_summary
        )
        doc_ids_used = [d["id"] for d in corpus]
        provider_label = f"{self.llm.active_provider}:{self.llm.model_name}"
        record_status = "draft" if analysis_type == "query" else "official"
        saved = await self.db.insert_analysis(
            query=query,
            response=response,
            document_ids=doc_ids_used,
            model=provider_label,
            analysis_type=analysis_type,
            executive_summary=parsed["executive_summary"],
            open_items_json=open_items_to_json(parsed["open_items"]),
            record_status=record_status,
            created_by=created_by,
            assessment_guidance=guidance_text or None,
        )
        return saved

    async def refine_custom_task(
        self,
        *,
        analysis_id: str,
        query: str,
        refinement: str,
        document_ids: list[str] | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        existing = await self.db.get_analysis_by_id(analysis_id)
        if not existing or existing.get("record_status") != "draft":
            raise ValueError("Only draft custom tasks can be refined")
        if existing.get("analysis_type") != "query":
            raise ValueError("Only custom task drafts can be refined")

        doc_ids = document_ids if document_ids else existing.get("document_ids") or None
        corpus = await self.store.get_corpus(doc_ids)
        corpus_text, coverage_notes = _format_corpus(corpus)
        doc_titles = [d["title"] for d in corpus if d.get("title")]
        title_list = "\n".join(f'- "{t}"' for t in doc_titles) if doc_titles else "[No documents stored]"

        prior_query = (existing.get("query") or "").strip()
        prior_summary = (existing.get("executive_summary") or "").strip()
        prior_response = (existing.get("response") or "").strip()
        prior_text = prior_summary
        if prior_response and prior_response not in prior_text:
            prior_text = f"{prior_text}\n\n{prior_response}".strip() if prior_text else prior_response

        refinement_text = refinement.strip() or "Improve clarity and completeness while keeping what already works."
        query_text = query.strip() or prior_query

        inventory = _format_document_inventory(corpus)
        coverage_section = (
            f"\n=== CORPUS COVERAGE NOTES ===\n{coverage_notes}\n"
            if coverage_notes
            else ""
        )

        prompt = f"""Use the following stored research and clinical material as your evidence base.
If the documents do not contain information needed to answer, state the gap explicitly.

=== DOCUMENT INVENTORY ({len(corpus)} documents in this assessment scope) ===
{inventory}
{coverage_section}
DOCUMENT TITLES — use these EXACT strings inside [SOURCE: Document "..."] tags:
{title_list}

=== STORED DOCUMENTS ===
{corpus_text}

=== PRIOR DRAFT (user wants a revision — not a brand-new task) ===
Original question:
{prior_query}

Previous draft:
{prior_text[:120000]}

=== REFINEMENT INSTRUCTIONS (apply these changes) ===
{refinement_text}

=== REVISED USER QUERY (answer this directly) ===
{query_text}

{_response_structure_for_analysis(analysis_type="query", query=query_text)}

This is a REFINEMENT run. Revise the prior draft to address the refinement instructions.
Keep strong sections that still fit; replace or expand parts the user asked to change.
Do NOT start from scratch unless the refinement instructions require it.

CRITICAL: Every factual bullet MUST end with [SOURCE: Document "..."] or another SOURCE tag.
Do NOT write parenthetical citations like (CT Report). Use [SOURCE: Document "exact title"] only.
Do NOT mention palliative care, hospice, or comfort care anywhere in the response.

Use clear ### headings for each section."""

        system = await build_medical_system_prompt(self.db)
        system = f"{system}\n\n{SOURCE_ATTRIBUTION_RULES}"

        response = await self.llm.generate(
            prompt=prompt,
            system=system,
            temperature=0.2,
        )

        response = filter_palliative_content(response)
        response, _attribution_level = enrich_with_sources(
            response, doc_titles, annotate_staging=True
        )
        parsed = parse_assessment(response)
        parsed["executive_summary"] = ensure_executive_summary(parsed, response)
        executive_summary, _ = enrich_with_sources(
            parsed["executive_summary"], doc_titles, annotate_staging=False
        )
        parsed["executive_summary"] = filter_palliative_content(executive_summary)
        doc_ids_used = [d["id"] for d in corpus]
        provider_label = f"{self.llm.active_provider}:{self.llm.model_name}"

        updated = await self.db.update_draft_analysis(
            analysis_id,
            query=query_text,
            response=response,
            document_ids=doc_ids_used,
            model=provider_label,
            executive_summary=parsed["executive_summary"],
            open_items_json=open_items_to_json(parsed["open_items"]),
        )
        if not updated:
            raise ValueError("Draft could not be updated")
        if created_by and not updated.get("created_by"):
            pass
        return updated

    async def summarize_documents(
        self,
        document_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self.analyze(
            query=(
                "Summarize each stored document for a clinical case conference. "
                "Extract clinically relevant findings, dates, test results, and recommendations. "
                "Note contradictions between sources."
            ),
            document_ids=document_ids,
            analysis_type="summarize",
        )
