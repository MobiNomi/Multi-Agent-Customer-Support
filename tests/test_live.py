"""Hits the real Groq API. Skipped unless GROQ_API_KEY is set: `pytest -m live`."""
import os

import pytest
from dotenv import dotenv_values

from app.config import PROJECT_ROOT

KEY = os.getenv("GROQ_API_KEY") or dotenv_values(PROJECT_ROOT / ".env").get("GROQ_API_KEY")
pytestmark = [pytest.mark.live, pytest.mark.skipif(not KEY, reason="GROQ_API_KEY not set")]


@pytest.fixture
def live_key(monkeypatch):
    from app import config
    from app.llm import get_llm
    monkeypatch.setenv("GROQ_API_KEY", KEY)
    config.get_settings.cache_clear()
    get_llm.cache_clear()


def test_router_classifies_billing(live_key):
    from langchain_core.messages import HumanMessage
    from app.graph.router import supervisor_router_node
    out = supervisor_router_node({"messages": [HumanMessage(content="I was charged twice for ORD-1034")]})
    assert out["intent"] == "billing"


def test_full_billing_turn(live_key):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        res = client.post("/api/v1/chat", json={"message": "I was charged twice for order ORD-1034",
                                                "customer_id": "CUS-1001"})
    assert res.status_code == 200
    data = res.json()
    assert data["assigned_agent"] == "billing_agent"
    assert "TXN-8922" in data["response"] or data["ticket_id"]


def test_policy_question_is_grounded(live_key):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        res = client.post("/api/v1/chat", json={"message": "Can I get a refund on an annual plan I bought 5 days ago?",
                                                "customer_id": "CUS-1001"})
    data = res.json()
    assert res.status_code == 200
    assert any("Refund policy" in s for s in data["sources"]), data
    assert "14" in data["response"], data
