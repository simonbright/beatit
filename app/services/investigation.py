from typing import Any

from app.services.content_policy import filter_palliative_content
from app.services.llm import LLMClient
from app.services.patient_context import DEFAULT_PATIENT_CONTEXT
from app.services.investigation_guidance import normalize_investigation_guidance
from app.services.source_policy import INVESTIGATION_PROMPT_TEMPLATE, SOURCE_ATTRIBUTION_RULES
from app.services.synthesis import build_medical_system_prompt, _format_corpus
from app.storage.database import Database
from app.storage.documents import DocumentStore


class InvestigationService:
    def __init__(
        self,
        store: DocumentStore,
        db: Database,
        llm: LLMClient | None = None,
    ):
        self.store = store
        self.db = db
        self.llm = llm or LLMClient()

    async def investigate_open_item(
        self,
        open_item_id: str,
        *,
        guidance: str | None = None,
    ) -> dict[str, Any]:
        item = await self.db.get_open_item(open_item_id)
        if not item:
            raise ValueError("Open item not found")

        analysis = await self.db.get_analysis_by_id(item["analysis_id"])
        document_ids = analysis.get("document_ids") if analysis else None
        corpus = await self.store.get_corpus(document_ids)
        corpus_text = _format_corpus(corpus)
        patient_context = (
            await self.db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
        )
        guidance_text = normalize_investigation_guidance(guidance)

        await self.db.update_open_item_status(open_item_id, "investigating")

        prompt = INVESTIGATION_PROMPT_TEMPLATE.format(
            item=item["item"],
            item_type=item["item_type"],
            corpus_text=corpus_text,
            patient_context=patient_context.strip(),
            guidance=guidance_text,
        )

        system = await build_medical_system_prompt(self.db)
        system = f"{system}\n\n{SOURCE_ATTRIBUTION_RULES}"

        try:
            response = await self.llm.generate(
                prompt=prompt,
                system=system,
                temperature=0.2,
            )
            response = filter_palliative_content(response)
            provider_label = f"{self.llm.active_provider}:{self.llm.model_name}"
            updated = await self.db.save_open_item_investigation_draft(
                open_item_id,
                response=response,
                model=provider_label,
                guidance=guidance_text if (guidance or "").strip() else None,
            )
            return updated
        except Exception:
            previous_status = (
                "pending_review"
                if item.get("investigation_draft_response")
                else "investigated"
                if item.get("investigation_response")
                else "open"
            )
            await self.db.update_open_item_status(open_item_id, previous_status)
            raise
