from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os


@dataclass(slots=True)
class Settings:
    app_name: str = "Prompt Grammar Tracker"
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8080"))
    data_dir: Path = Path(os.getenv("DATA_DIR", "data")).resolve()
    import_dir: Path = Path(os.getenv("IMPORT_DIR", "imports")).resolve()
    import_poll_interval_seconds: int = int(
        os.getenv("IMPORT_POLL_INTERVAL_SECONDS", "20")
    )
    analyzer_mode: str = os.getenv("ANALYZER_MODE", "auto")
    analyzer_language: str = os.getenv("ANALYZER_LANGUAGE", "en-US")
    llm_api_base_url: str = os.getenv("LLM_API_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "256"))
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "none")
    llm_seed: int | None = (
        int(os.getenv("LLM_SEED", "0")) if os.getenv("LLM_SEED", "").strip() else None
    )
    api_token: str = os.getenv("APP_API_TOKEN", "")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.import_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
