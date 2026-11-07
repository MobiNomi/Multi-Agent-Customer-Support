"""Central configuration. Every connection setting is read here and nowhere else."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root no matter where the process was started from.
# Existing environment variables win over .env values.
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "support_system.db"


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    sql_echo: bool
    seed_demo_data: bool
    groq_api_key: str | None
    llm_model: str
    llm_temperature: float
    llm_timeout: float
    llm_max_retries: int
    max_graph_steps: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}"),
        sql_echo=_bool(os.getenv("SQL_ECHO"), False),
        seed_demo_data=_bool(os.getenv("SEED_DEMO_DATA"), True),
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL", "openai/gpt-oss-20b"),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
        llm_timeout=float(os.getenv("LLM_TIMEOUT", "30")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        max_graph_steps=int(os.getenv("MAX_GRAPH_STEPS", "12")),
    )
