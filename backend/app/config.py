from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    data_mode: Literal["mock", "local"] = "mock"
    mcp_enabled: bool = True
    allowed_co_codes: str = "DEMO01,DEMO02"

    sqlite_path: str = "data/local/financial.sqlite3"
    sqlite_read_only: bool = True

    neo4j_uri: str = "neo4j://127.0.0.1:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "poc_password_neo4j"
    neo4j_database: str = "neo4j"
    neo4j_vector_index: str = "chunk_embedding_v1"
    neo4j_fulltext_index: str = "chunk_fulltext_v1"

    ollama_url: str = "http://127.0.0.1:11434"
    ollama_embedding_model: str = "qwen3-embedding"

    company_llm_mode: Literal["mock", "openai_compatible"] = "mock"
    company_llm_base_url: str = "https://company-llm.example.com/v1"
    company_llm_api_key: str = ""
    company_llm_model: str = "company-model"
    company_llm_timeout_seconds: float = 30.0

    knowledge_mcp_url: str = "http://127.0.0.1:8001/mcp"
    finance_mcp_url: str = "http://127.0.0.1:8002/mcp"
    mcp_shared_token: str = "change-me"
    mcp_server_host: str = "127.0.0.1"
    knowledge_mcp_port: int = 8001
    finance_mcp_port: int = 8002

    @property
    def sqlite_database_path(self) -> Path:
        path = Path(self.sqlite_path).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @property
    def allowed_co_code_set(self) -> set[str]:
        return {
            value.strip().upper()
            for value in self.allowed_co_codes.split(",")
            if value.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
