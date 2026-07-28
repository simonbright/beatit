from typing import Any
from collections.abc import AsyncIterator
import json

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

    def _raise_for_api_error(self, response: httpx.Response) -> None:
        message = None
        try:
            payload = response.json()
            message = (payload.get("error") or {}).get("message")
        except Exception:
            pass
        if message:
            hint = ""
            if "No endpoints found" in message or "not a valid model" in message:
                hint = (
                    f" Model '{self.model}' is unavailable on OpenRouter. "
                    "Open Settings and choose a different model."
                )
            raise RuntimeError(f"OpenRouter: {message}.{hint}")
        response.raise_for_status()

    async def generate(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat(messages=messages, temperature=temperature)

    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
    ) -> str:
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter API key not configured. Set OPENROUTER_API_KEY in .env"
            )

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
            if response.status_code >= 400:
                self._raise_for_api_error(response)
            data = response.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        return content.strip()

    async def stream_chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        if not self.api_key:
            raise RuntimeError(
                "OpenRouter API key not configured. Set OPENROUTER_API_KEY in .env"
            )

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "stream": True,
                },
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_api_error(response)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    token = delta.get("content") or ""
                    if token:
                        yield token
