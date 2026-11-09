"""Central configuration. Every connection setting is read here and nowhere else."""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root no matter where the process was started from.
# Existing environment variables win over .env values.
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "support_system.db"
DEFAULT_MEMORY_PATH = PROJECT_ROOT / "data" / "checkpoints.db"
DEFAULT_KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    sql_echo: bool
    seed_demo_data: bool
    memory_backend: str
    memory_db_path: Path
    groq_api_key: str | None
    llm_model: str
    llm_temperature: float
    llm_timeout: float
    llm_max_retries: int
    max_graph_steps: int
    max_tool_rounds: int
    knowledge_dir: Path
    chunk_max_chars: int
    chunk_overlap: int
    retrieval_top_k: int
    demo_mode: bool
    auth_secret: Optional[str]
    access_token_minutes: int
    login_max_attempts: int
    login_lock_minutes: int
    cookie_secure: bool
    registration_enabled: bool
    registrations_per_hour: int
    guardrails_enabled: bool
    chat_messages_per_minute: int
    max_tickets_per_hour: int
    cors_origins: tuple


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}"),
        sql_echo=_bool(os.getenv("SQL_ECHO"), False),
        seed_demo_data=_bool(os.getenv("SEED_DEMO_DATA"), True),
        memory_backend=os.getenv("MEMORY_BACKEND", "sqlite").strip().lower(),
        memory_db_path=Path(os.getenv("MEMORY_DB_PATH") or DEFAULT_MEMORY_PATH),
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL", "openai/gpt-oss-20b"),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.0")),
        llm_timeout=float(os.getenv("LLM_TIMEOUT", "30")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        max_graph_steps=int(os.getenv("MAX_GRAPH_STEPS", "20")),
        max_tool_rounds=int(os.getenv("MAX_TOOL_ROUNDS", "4")),
        knowledge_dir=Path(os.getenv("KNOWLEDGE_DIR") or DEFAULT_KNOWLEDGE_DIR),
        chunk_max_chars=int(os.getenv("CHUNK_MAX_CHARS", "1200")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "150")),
        retrieval_top_k=int(os.getenv("RETRIEVAL_TOP_K", "4")),
        demo_mode=_bool(os.getenv("DEMO_MODE"), True),
        auth_secret=os.getenv("AUTH_SECRET") or None,
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "480")),
        login_max_attempts=int(os.getenv("LOGIN_MAX_ATTEMPTS", "5")),
        login_lock_minutes=int(os.getenv("LOGIN_LOCK_MINUTES", "15")),
        cookie_secure=_bool(os.getenv("COOKIE_SECURE"), False),
        registration_enabled=_bool(os.getenv("REGISTRATION_ENABLED"), True),
        registrations_per_hour=int(os.getenv("REGISTRATIONS_PER_HOUR", "5")),
        guardrails_enabled=_bool(os.getenv("GUARDRAILS_ENABLED"), True),
        chat_messages_per_minute=int(os.getenv("CHAT_MESSAGES_PER_MINUTE", "10")),
        max_tickets_per_hour=int(os.getenv("MAX_TICKETS_PER_HOUR", "3")),
        cors_origins=tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()),
    )
