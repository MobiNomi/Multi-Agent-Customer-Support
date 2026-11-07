import pytest
from sqlalchemy.exc import IntegrityError

from app import config
from app.database import connection
from app.database.models import Customer, Order
from app.llm import LLMNotConfiguredError, get_llm


def test_default_sqlite_path_is_absolute(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config.get_settings.cache_clear()
    url = config.get_settings().database_url
    assert url == f"sqlite:///{config.DEFAULT_SQLITE_PATH}"
    assert config.DEFAULT_SQLITE_PATH.is_absolute()  # no longer depends on the working directory


def test_engine_is_created_once():
    assert connection.get_engine() is connection.get_engine()


def test_check_db_reports_healthy():
    assert connection.check_db() is True


def test_session_scope_commits():
    with connection.session_scope() as s:
        s.add(Customer(id="CUS-2000", name="New", email="new@example.com"))
    with connection.session_scope() as s:
        assert s.get(Customer, "CUS-2000") is not None


def test_session_scope_rolls_back_on_error():
    with pytest.raises(RuntimeError):
        with connection.session_scope() as s:
            s.add(Customer(id="CUS-3000", name="Ghost", email="ghost@example.com"))
            s.flush()
            raise RuntimeError("boom")
    with connection.session_scope() as s:
        assert s.get(Customer, "CUS-3000") is None


def test_sqlite_enforces_foreign_keys():
    with pytest.raises(IntegrityError):
        with connection.session_scope() as s:
            s.add(Order(id="ORD-X", customer_id="CUS-DOES-NOT-EXIST", amount=1.0, status="Processing"))


def test_llm_missing_key_raises_clear_error():
    with pytest.raises(LLMNotConfiguredError, match="GROQ_API_KEY"):
        get_llm()


def test_app_imports_without_api_key():
    import app.main  # noqa: F401  (used to crash at import time)
