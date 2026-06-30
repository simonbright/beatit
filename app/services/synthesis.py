from typing import Any

from app.services.llm import LLMClient
from app.storage.database import Database
from app.storage.documents import DocumentStore

MEDICAL_SYSTEM_PROMPT = """You are an experienced medical oncologist assisting with case review and treatment planning discussion.

Patient context (baseline — update as new evidence arrives):
- Woman in her 70s
- Recently diagnosed with pancreatic cancer
- Possible liver metastasis (staging and extent not yet fully confirmed)

Your role:
1. Synthesize available clinical and research material clearly and accurately
2. Distinguish what is KNOWN vs UNKNOWN vs UNCERTAIN
3. Identify critical information gaps that should be closed before major treatment decisions
4. Outline a broad range of treatment options (surgery, systemic therapy, radiation, clinical trials, molecular testing, staging workup)
5. Note where evidence is strong vs where decisions depend on patient-specific factors (performance status, comorbidities, goals of care, biomarkers)

Important constraints:
- Do not discuss, recommend, or mention palliative care
- This is decision-support for clinicians and informed family discussion — NOT a substitute for in-person oncology care
- Do not invent lab values, imaging findings, or staging that are not in the provided documents
- When data is missing, say so explicitly and explain why it matters
- Use clear headings and bullet points
- Flag urgent red-flag symptoms or scenarios when relevant
- Be compassionate but clinically precise"""


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
    ) -> dict[str, Any]:
        corpus = await self.store.get_corpus(document_ids)
        corpus_text = _format_corpus(corpus)

        if include_baseline_assessment and not query.strip():
            query = (
                "Provide a comprehensive baseline oncology assessment: what we know, "
                "what we do not know, critical gaps to close, staging considerations, "
                "and a broad overview of treatment options for this pancreatic cancer case "
                "with possible liver metastasis."
            )

        prompt = f"""Use the following stored research and clinical material as your evidence base.
If the documents do not contain information needed to answer, state the gap explicitly.

=== STORED DOCUMENTS ===
{corpus_text}

=== USER QUERY ===
{query}

Structure your response with these sections when applicable:
1. Executive summary
2. What we know (with source references by document title)
3. What we do not know / uncertainties
4. Critical gaps to close (prioritized)
5. Staging & workup considerations
6. Treatment options (broad range, with rationale and prerequisites)
7. Questions for the oncology team
8. Disclaimer"""

        response = await self.llm.generate(
            prompt=prompt,
            system=MEDICAL_SYSTEM_PROMPT,
            temperature=0.3,
        )

        doc_ids_used = [d["id"] for d in corpus]
        provider_label = f"{self.llm.active_provider}:{self.llm.model_name}"
        saved = await self.db.insert_analysis(
            query=query,
            response=response,
            document_ids=doc_ids_used,
            model=provider_label,
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
        )
