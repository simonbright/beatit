from typing import Any

from app.services.assessment_parse import ensure_executive_summary, open_items_to_json, parse_assessment
from app.services.llm import LLMClient
from app.services.content_policy import PALLIATIVE_EXCLUSION, filter_palliative_content
from app.services.source_policy import (
    BASELINE_GAP_RULES,
    BASELINE_GUIDANCE_SECTION,
    CUSTOM_QUERY_RESPONSE_STRUCTURE,
    LIST_ITEM_SOURCE_RULES,
    RESPONSE_STRUCTURE_WITH_SOURCES,
    SOURCE_ATTRIBUTION_RULES,
    TRIAL_SEARCH_QUERY_INSTRUCTIONS,
)
from app.services.source_normalize import enrich_with_sources
from app.storage.database import Database
from app.storage.documents import DocumentStore
from app.services.patient_context import DEFAULT_PATIENT_CONTEXT

MEDICAL_SYSTEM_TEMPLATE = """You are an experienced medical oncologist assisting with case review and treatment planning discussion.

Patient context (baseline — update as new evidence arrives):
{patient_context}

Your role:
1. Synthesize available clinical and research material clearly and accurately
2. Distinguish what is KNOWN vs UNKNOWN vs UNCERTAIN
3. Identify critical information gaps that should be closed before major treatment decisions
4. Outline a broad range of treatment options (surgery, systemic therapy, radiation, clinical trials, molecular testing, staging workup)
5. Note where evidence is strong vs where decisions depend on patient-specific factors (performance status, comorbidities, goals of care, biomarkers)

Important constraints:
- Do not discuss, recommend, or mention palliative care — see PALLIATIVE EXCLUSION below
- This is decision-support for clinicians and informed family discussion — NOT a substitute for in-person oncology care
- Do not invent lab values, imaging findings, or staging that are not in the provided documents
- When data is missing, say so explicitly and explain why it matters
- Use clear headings and bullet points
- Flag urgent red-flag symptoms or scenarios when relevant
- Be compassionate but clinically precise"""


async def build_medical_system_prompt(db: Database) -> str:
    context = await db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
    base = MEDICAL_SYSTEM_TEMPLATE.format(patient_context=context.strip())
    return f"{base}\n\n{PALLIATIVE_EXCLUSION}"


def _format_corpus(corpus: list[dict[str, Any]], max_chars: int = 120_000) -> str:
    sections = []
    used = 0
    for item in corpus:
        header = (
            f"### [{item['source_type'].upper()}] {item['title']}\n"
            f"ID: {item['id']}\n"
            f"Source: {item.get('source_uri') or 'local'}\n"
        )
        body = item["text"]
        chunk = f"{header}\n{body}\n"
        if used + len(chunk) > max_chars:
            remaining = max_chars - used - len(header) - 50
            if remaining > 500:
                sections.append(f"{header}\n{body[:remaining]}\n...[truncated]\n")
            break
        sections.append(chunk)
        used += len(chunk)
    return "\n---\n".join(sections) if sections else "[No document text available]"


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


def _response_structure_for_analysis(*, analysis_type: str, query: str) -> str:
    if analysis_type == "query":
        structure = f"{CUSTOM_QUERY_RESPONSE_STRUCTURE}\n\n{LIST_ITEM_SOURCE_RULES}"
        if _is_trial_search_query(query):
            structure = f"{structure}\n\n{TRIAL_SEARCH_QUERY_INSTRUCTIONS}"
        return structure
    return RESPONSE_STRUCTURE_WITH_SOURCES


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
    ) -> dict[str, Any]:
        corpus = await self.store.get_corpus(document_ids)
        corpus_text = _format_corpus(corpus)
        doc_titles = [d["title"] for d in corpus if d.get("title")]
        title_list = "\n".join(f'- "{t}"' for t in doc_titles) if doc_titles else "[No documents stored]"

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

        if include_baseline_assessment and not query.strip():
            analysis_type = "baseline"
            query = (
                "Provide a comprehensive baseline oncology assessment: what we know, "
                "what we do not know, critical gaps to close, staging considerations, "
                "and a broad overview of treatment options for this pancreatic cancer case "
                "with possible liver metastasis."
            )

        gap_rules = f"\n{BASELINE_GAP_RULES}\n" if analysis_type == "baseline" else ""
        guidance_text = (assessment_guidance or "").strip()
        guidance_section = (
            BASELINE_GUIDANCE_SECTION.format(guidance=guidance_text)
            if analysis_type == "baseline" and guidance_text
            else ""
        )

        prompt = f"""Use the following stored research and clinical material as your evidence base.
If the documents do not contain information needed to answer, state the gap explicitly.

DOCUMENT TITLES — use these EXACT strings inside [SOURCE: Document "..."] tags:
{title_list}

=== STORED DOCUMENTS ===
{corpus_text}
{prior_section}{guidance_section}
=== USER QUERY (answer this directly — this is the primary task) ===
{query}
{gap_rules}
{_response_structure_for_analysis(analysis_type=analysis_type, query=query)}

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
        corpus_text = _format_corpus(corpus)
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

        prompt = f"""Use the following stored research and clinical material as your evidence base.
If the documents do not contain information needed to answer, state the gap explicitly.

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
                "Summarize each stored document for an oncology case conference. "
                "Extract clinically relevant findings, dates, test results, and recommendations. "
                "Note contradictions between sources."
            ),
            document_ids=document_ids,
            analysis_type="summarize",
        )
