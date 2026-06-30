from typing import Any

from app.services.assessment_parse import open_items_to_json, parse_assessment
from app.services.llm import LLMClient
from app.services.content_policy import PALLIATIVE_EXCLUSION, filter_palliative_content
from app.services.source_policy import (
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
        analysis_type: str = "query",
        created_by: str | None = None,
    ) -> dict[str, Any]:
        corpus = await self.store.get_corpus(document_ids)
        corpus_text = _format_corpus(corpus)
        doc_titles = [d["title"] for d in corpus if d.get("title")]
        title_list = "\n".join(f'- "{t}"' for t in doc_titles) if doc_titles else "[No documents stored]"

        if include_baseline_assessment and not query.strip():
            analysis_type = "baseline"
            query = (
                "Provide a comprehensive baseline oncology assessment: what we know, "
                "what we do not know, critical gaps to close, staging considerations, "
                "and a broad overview of treatment options for this pancreatic cancer case "
                "with possible liver metastasis."
            )

        prompt = f"""Use the following stored research and clinical material as your evidence base.
If the documents do not contain information needed to answer, state the gap explicitly.

DOCUMENT TITLES — use these EXACT strings inside [SOURCE: Document "..."] tags:
{title_list}

=== STORED DOCUMENTS ===
{corpus_text}

=== USER QUERY (answer this directly — this is the primary task) ===
{query}

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
        )
        return saved

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
