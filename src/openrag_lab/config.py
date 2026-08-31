"""Application configuration loaded from environment / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings for OpenRAG Lab."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrag_base_url: str = Field(default="http://localhost:3000", alias="OPENRAG_BASE_URL")
    openrag_api_key: str = Field(default="", alias="OPENRAG_API_KEY")
    openrag_knowledge_filter: str = Field(default="", alias="OPENRAG_KNOWLEDGE_FILTER")

    eval_csv: Path = Field(default=PROJECT_ROOT / "configs/eval/fintech-eval.csv", alias="EVAL_CSV")
    dify_rag_lab_path: Path = Field(default=Path("../dify-rag-lab"), alias="DIFY_RAG_LAB_PATH")
    dify_sample_data_path: Path = Field(default=Path("../dify-rag-lab/sample-data"), alias="DIFY_SAMPLE_DATA_PATH")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
