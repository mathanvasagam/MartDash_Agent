from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    telegram_bot_token: str = ""
    database_url: str = "sqlite:///./tele_agent.db"
    store_state: str = "Tamil Nadu"
    agent_max_tool_rounds: int = 8

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def validate_runtime(self) -> None:
        missing = []
        if not self.groq_api_key:
            missing.append("GROQ_API_KEY")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if missing:
            raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
        if self.app_env.lower() == "production" and self.database_url.startswith("sqlite"):
            raise RuntimeError(
                "Production must use PostgreSQL for safe concurrent stock transactions."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
