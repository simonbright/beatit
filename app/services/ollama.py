from typing import Any
from collections.abc import AsyncIterator
import json

import httpx

from app.config import settings


def ollama_error_message(response: httpx.Response) -> str:
    """Extract a useful error from an Ollama HTTP response."""
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error")
            if err:
                text = str(err).strip()
                if "unknown model architecture: 'mllama'" in text:
                    return (
                        "Ollama on the VM cannot load llama3.2-vision (missing mllama support). "
                        "Reinstall the latest Ollama on the VM, restart it, then retry. "
                        "Or set OLLAMA_VISION_MODEL=moondream after running: ollama pull moondream"
                    )
                if "llama-server process has terminated" in text:
                    return text.split("\n")[0].strip() or text
                return text
    except Exception:
        pass
    return f"Ollama request failed ({response.status_code})"


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self.model

    async def health(self) -> dict[str, Any]:
        # Short connect timeout — firewall drops to the VM must not stall the UI.
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            models = [m.get("name") for m in data.get("models", [])]
            return {
                "connected": True,
                "base_url": self.base_url,
                "configured_model": self.model,
                "configured_vision_model": settings.ollama_vision_model,
                "available_models": models,
                "model_available": any(
                    self.model in (name or "") for name in models
                ),
                "vision_model_available": any(
                    settings.ollama_vision_model in (name or "") for name in models
                ),
            }

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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        timeout = httpx.Timeout(
            settings.ollama_generate_timeout,
            connect=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            if not response.is_success:
                raise RuntimeError(ollama_error_message(response))
            data = response.json()
            message = data.get("message") or {}
            return str(message.get("content") or "").strip()

    async def stream_chat(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        timeout = httpx.Timeout(
            settings.ollama_generate_timeout,
            connect=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise RuntimeError(
                        f"Ollama request failed ({response.status_code}): "
                        f"{body.decode(errors='ignore')[:400]}"
                    )
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise RuntimeError(str(data["error"]))
                    message = data.get("message") or {}
                    token = message.get("content") or ""
                    if token:
                        yield token
                    if data.get("done"):
                        break


class OllamaVisionClient(OllamaClient):
    """Ollama chat API with image input for DICOM slice reads."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ):
        super().__init__(
            base_url=base_url,
            model=model or settings.ollama_vision_model,
        )

    async def describe_image(
        self,
        *,
        image_b64: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        if "moondream" in self.model.lower():
            # Moondream via Ollama ignores system role; keep one user turn.
            if system:
                prompt = f"{system}\n\n{prompt}"
            system = None

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64],
            }
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        timeout = httpx.Timeout(
            settings.ollama_generate_timeout,
            connect=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            if not response.is_success:
                raise RuntimeError(ollama_error_message(response))
            data = response.json()
            message = data.get("message") or {}
            content = message.get("content") or ""
            return str(content).strip()
