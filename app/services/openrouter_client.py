from typing import Any

import httpx

from app.config import settings


class OpenRouterClient:
    """OpenAI-compatible chat client for OpenRouter."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.model = model or settings.openrouter_model
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def model_name(self) -> str:
        return self.model

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if settings.openrouter_http_referer:
            headers["HTTP-Referer"] = settings.openrouter_http_referer
        if settings.openrouter_app_title:
            headers["X-Title"] = settings.openrouter_app_title
        return headers

    async def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "connected": False,
                "configured": False,
                "provider": self.provider_name,
                "model": self.model,
                "error": "OPENROUTER_API_KEY not set in .env",
            }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                response.raise_for_status()
            return {
                "connected": True,
                "configured": True,
                "provider": self.provider_name,
                "model": self.model,
                "base_url": self.base_url,
            }
        except Exception as exc:
            return {
                "connected": False,
                "configured": True,
                "provider": self.provider_name,
                "model": self.model,
                "base_url": self.base_url,
                "error": str(exc),
            }

    async def generate(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter API key not configured. Set OPENROUTER_API_KEY in .env"
            )

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                },
            )
            response.raise_for_status()
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        return content.strip()
