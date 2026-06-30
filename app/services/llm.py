from typing import Any, Protocol

from app.config import settings
from app.services.ollama import OllamaClient
from app.services.openrouter_client import OpenRouterClient


class LLMBackend(Protocol):
    provider_name: str
    model_name: str

    async def health(self) -> dict[str, Any]: ...
    async def generate(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str: ...


class LLMClient:
    """Routes analysis to OpenRouter (default) or Ollama, with optional auto-fallback."""

    def __init__(
        self,
        provider: str | None = None,
        ollama: OllamaClient | None = None,
        openrouter: OpenRouterClient | None = None,
    ):
        self.provider = (provider or settings.llm_provider).lower().strip()
        self.ollama = ollama or OllamaClient()
        self.openrouter = openrouter or OpenRouterClient()
        self._active: LLMBackend | None = None
        self._active_provider: str | None = None

    @property
    def model_name(self) -> str:
        if self._active:
            return self._active.model_name
        if self.provider == "ollama":
            return self.ollama.model
        return self.openrouter.model_name

    @property
    def active_provider(self) -> str:
        return self._active_provider or self.provider

    async def _ollama_ready(self) -> bool:
        try:
            status = await self.ollama.health()
            return bool(status.get("connected") and status.get("model_available"))
        except Exception:
            return False

    async def _openrouter_ready(self) -> bool:
        status = await self.openrouter.health()
        return bool(status.get("connected"))

    async def _resolve_backend(self) -> LLMBackend:
        if self.provider == "ollama":
            if not await self._ollama_ready():
                raise RuntimeError(
                    "Ollama is not reachable or the configured model is unavailable. "
                    "Check OLLAMA_BASE_URL and OLLAMA_MODEL, or set LLM_PROVIDER=openrouter."
                )
            self._active = self.ollama
            self._active_provider = "ollama"
            return self.ollama

        if self.provider == "openrouter":
            if not await self._openrouter_ready():
                status = await self.openrouter.health()
                raise RuntimeError(
                    status.get("error")
                    or "OpenRouter is not configured. Set OPENROUTER_API_KEY in .env."
                )
            self._active = self.openrouter
            self._active_provider = "openrouter"
            return self.openrouter

        # auto: prefer Ollama when ready, otherwise OpenRouter
        if await self._ollama_ready():
            self._active = self.ollama
            self._active_provider = "ollama"
            return self.ollama

        if await self._openrouter_ready():
            self._active = self.openrouter
            self._active_provider = "openrouter"
            return self.openrouter

        raise RuntimeError(
            "No LLM available. Set OPENROUTER_API_KEY or configure Ollama on your remote server."
        )

    async def health(self) -> dict[str, Any]:
        ollama_status = await self._safe_health(self.ollama)
        openrouter_status = await self._safe_health(self.openrouter)

        active = None
        active_provider = self.provider
        try:
            backend = await self._resolve_backend()
            active = {
                "provider": self._active_provider,
                "model": backend.model_name,
                "ready": True,
            }
            active_provider = self._active_provider or self.provider
        except Exception as exc:
            active = {"provider": None, "model": None, "ready": False, "error": str(exc)}

        return {
            "configured_provider": self.provider,
            "active": active,
            "ollama": ollama_status,
            "openrouter": openrouter_status,
            "active_provider": active_provider,
        }

    async def _safe_health(self, client: LLMBackend) -> dict[str, Any]:
        try:
            return await client.health()
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    async def generate(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        backend = await self._resolve_backend()
        return await backend.generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
        )
