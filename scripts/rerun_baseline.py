#!/usr/bin/env python3
"""Enqueue a baseline analysis job and poll until it finishes."""
from __future__ import annotations

import asyncio
import json
import sys
import time

from app.services.analysis_jobs import enqueue_analysis_job, get_job_payload

DOC_IDS = [
    "4c08bac2-3ed1-48ac-8ea0-5005e5aad91c",
    "4915581a-8cec-4804-b75d-6e1d458816f5",
    "5e377976-4909-4685-bc2f-14769faf0e74",
    "cf5fc525-d973-455c-9841-3a399030c9f6",
    "137247db-8528-47bb-90ae-fa4da7340e02",
    "ca7505ed-4dd9-4a3d-a1d1-352e4604ea11",
    "0268d4bf-602e-41ae-ad4d-20cba62ce931",
    "ccef43ef-526b-44ae-bf76-5bb3f5d7f92e",
    "25b81827-5755-411b-bd91-92a18db39600",
    "4c17b31f-12b0-4b3d-ac61-919da0590b07",
]


async def wait_for_ollama(max_wait_seconds: int = 600) -> bool:
    from app.services.ollama import OllamaClient

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        try:
            status = await OllamaClient().health()
            if status.get("connected") and status.get("model_available"):
                print("Ollama ready:", status.get("configured_model"))
                return True
        except Exception as exc:
            print(f"Waiting for Ollama… ({exc})")
        await asyncio.sleep(15)
    return False


async def main() -> int:
    from app.config import settings

    print(f"LLM_PROVIDER={settings.llm_provider}")
    print(f"OLLAMA_BASE_URL={settings.ollama_base_url}")
    print(f"OLLAMA_MODEL={settings.ollama_model}")

    if settings.llm_provider == "auto":
        print("Note: auto mode falls back to OpenRouter when Ollama is down.")
        print("Waiting for Ollama so this rerun uses the VM model…")

    if not await wait_for_ollama():
        print("ERROR: Ollama still unreachable. Open Tailscale on your Mac and start Ollama on the VM.")
        return 1

    job = await enqueue_analysis_job(
        job_type="baseline",
        query="",
        document_ids=DOC_IDS,
        include_baseline_assessment=True,
        requested_by="rerun-script",
    )
    job_id = job["id"]
    started = time.time()
    print(f"Started baseline job {job_id} ({len(DOC_IDS)} documents)")

    while True:
        await asyncio.sleep(5)
        payload = await get_job_payload(job_id)
        if not payload:
            print("Job disappeared")
            return 1
        status = payload["status"]
        elapsed = int(time.time() - started)
        print(f"[{elapsed}s] status={status}")
        if status == "completed":
            print("SUCCESS analysis_id=", payload.get("analysis_id"))
            return 0
        if status in {"failed", "cancelled"}:
            print("FAILED:", payload.get("error") or status)
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
