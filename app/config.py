import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM: openrouter (default), ollama, or auto (Ollama when ready, else OpenRouter)
    llm_provider: str = "openrouter"

    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-3.1-flash-lite"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_http_referer: str = "http://localhost:8080"
    openrouter_app_title: str = "BeatIt"

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_vision_model: str = "moondream"
    # Seconds to wait for an Ollama generation. CPU-only models can be slow,
    # so default generously (30 min). Tune via OLLAMA_GENERATE_TIMEOUT.
    ollama_generate_timeout: float = 1800.0

    # Secured access (required on Render — comma-separated usernames, shared password)
    auth_username: str = ""
    auth_password: str = ""
    # Optional per-user passwords: email:password,email2:password2
    # Overrides AUTH_PASSWORD for those usernames only.
    auth_user_passwords: str = ""
    auth_secret: str = ""
    render: bool = False
    public_url: str = ""

    data_dir: Path = Path("./data")
    host: str = "0.0.0.0"
    port: int = 8080

    @property
    def auth_usernames(self) -> list[str]:
        return [u.strip() for u in self.auth_username.split(",") if u.strip()]

    @property
    def auth_password_by_user(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for part in self.auth_user_passwords.split(","):
            entry = part.strip()
            if not entry or ":" not in entry:
                continue
            user, password = entry.split(":", 1)
            user = user.strip()
            password = password.strip()
            if user and password:
                mapping[user] = password
        return mapping

    def password_for(self, username: str) -> str:
        username = username.strip()
        return self.auth_password_by_user.get(username) or self.auth_password

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_usernames and (self.auth_password or self.auth_password_by_user))

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "beatit.db"


settings = Settings()

# Render and other PaaS providers inject PORT.
_env_port = os.getenv("PORT")
if _env_port:
    settings.port = int(_env_port)

# Prefer explicit public URL, then Render's auto-set external URL.
_public_url = os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")
if _public_url:
    settings.public_url = _public_url.rstrip("/")
    settings.openrouter_http_referer = settings.public_url

if os.getenv("RENDER"):
    settings.render = True
