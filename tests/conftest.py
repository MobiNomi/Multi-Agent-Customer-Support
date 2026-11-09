import pytest

from app import config
from app.database import connection
from app.database.seed import seed_database
from app.llm import get_llm
from app.rag.knowledge_base import get_knowledge_base
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own seeded SQLite file and no real API key."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("SEED_DEMO_DATA", "false")
    monkeypatch.setenv("MEMORY_BACKEND", "sqlite")
    monkeypatch.setenv("MEMORY_DB_PATH", str(tmp_path / "checkpoints.db"))
    config.get_settings.cache_clear()
    get_llm.cache_clear()
    get_knowledge_base.cache_clear()
    connection.reset_engine()
    seed_database()
    yield
    connection.reset_engine()
    config.get_settings.cache_clear()
    get_llm.cache_clear()


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch the model into the router and the agents. Script replies via fake_llm.agent_replies."""
    fake = FakeLLM()
    monkeypatch.setattr("app.graph.agents.get_llm", lambda: fake)
    monkeypatch.setattr("app.graph.router.get_llm", lambda: fake)
    return fake
