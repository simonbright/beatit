from typing import Any

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
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            models = [m.get("name") for m in data.get("models", [])]
            return {
                "connected": True,
                "base_url": self.base_url,
                "configured_model": self.model,
                "available_models": models,
                "model_available": any(
                    self.model in (name or "") for name in models
                ),
            }

    async def generate(
        self,
        *,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system

        timeout = httpx.Timeout(
            settings.ollama_generate_timeout,
            connect=10.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            if not response.is_success:
                raise RuntimeError(ollama_error_message(response))
            data = response.json()
            return data.get("response", "").strip()


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
