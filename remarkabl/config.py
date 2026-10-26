from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    device_token: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_base: str = "https://openrouter.ai/api/v1"
    telegram_bot_token: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_allowed_user_id: int = 241154130
    telegram_api_base: str = ""
    telegram_local_dir: Path | None = None
    rmfakecloud_url: str = ""
    rmfakecloud_user: str = ""
    rmfakecloud_password: str = ""
    embedding_mode: str = "semantic"
    embedding_model: str = "intfloat/multilingual-e5-small"
    max_document_mb: int = Field(default=200, ge=1, le=2000)
    max_pdf_pages: int = Field(default=1500, ge=1)
    max_image_bytes: int = 8 * 1024 * 1024
    max_body_bytes: int = 16 * 1024 * 1024
    mock_llm: bool = False
    run_workers: bool = True
    retry_base_seconds: float = 15
    max_attempts: int = 6
    document_font: Path | None = None

    @property
    def telegram_dir(self) -> Path:
        return self.telegram_local_dir or self.data_dir / "telegram"

    @property
    def telegram_base(self) -> str:
        if self.telegram_api_base:
            return self.telegram_api_base.rstrip("/")
        if self.telegram_api_id and self.telegram_api_hash:
            return "http://127.0.0.1:8081"
        return "https://api.telegram.org"

    @property
    def max_document_bytes(self) -> int:
        return self.max_document_mb * 1024 * 1024
