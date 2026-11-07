import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.main import app
from tests.fakes import tool_call


@pytest.fixture
def client():
    with TestClient(app) as c:  # context manager runs the lifespan (init_db + graph)
        yield c


def test_health_check(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["llm"] == "missing GROQ_API_KEY"


def test_chat_billing_flow(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD-1034"}),
        tool_call("create_support_ticket", {"category": "Billing", "description": "Refund TXN-8922"},
                  call_id="call_2"),
        AIMessage(content="TXN-8922 was a duplicate on ORD-1034; I've opened a refund request."),
    ]
    res = client.post("/api/v1/chat", json={"message": "I was charged twice for order ORD-1034",
                                            "customer_id": "CUS-1001"})
    assert res.status_code == 200
    data = res.json()
    assert data["assigned_agent"] == "billing_agent"
    assert data["intent"] == "billing"
    assert data["ticket_id"].startswith("TCK-")
    assert "ORD-1034" in data["response"]
    assert data["session_id"]


def test_session_continues_conversation(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="Hi Ali!"), AIMessage(content="Still here.")]
    first = client.post("/api/v1/chat", json={"message": "hello", "customer_id": "CUS-1001"}).json()
    second = client.post("/api/v1/chat", json={"message": "are you there?", "customer_id": "CUS-1001",
                                               "session_id": first["session_id"]}).json()
    assert second["session_id"] == first["session_id"]
    assert second["response"] == "Still here."
    # The second model call received the first exchange as history.
    contents = [m.content for m in fake_llm.calls[-1]]
    assert "hello" in contents and "Hi Ali!" in contents


def test_same_session_id_different_customer_is_separate(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="one"), AIMessage(content="two")]
    client.post("/api/v1/chat", json={"message": "secret", "customer_id": "CUS-1001", "session_id": "s1"})
    client.post("/api/v1/chat", json={"message": "hi", "customer_id": "CUS-1002", "session_id": "s1"})
    assert "secret" not in [m.content for m in fake_llm.calls[-1]]


def test_ticket_id_resets_each_turn(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="ok")]
    first = client.post("/api/v1/chat", json={"message": "I need a human", "customer_id": "CUS-1001"}).json()
    assert first["ticket_id"]
    second = client.post("/api/v1/chat", json={"message": "thanks", "customer_id": "CUS-1001",
                                               "session_id": first["session_id"]}).json()
    assert second["ticket_id"] is None and second["needs_human"] is False


def test_unknown_customer_returns_404(client, fake_llm):
    res = client.post("/api/v1/chat", json={"message": "hi", "customer_id": "CUS-9999"})
    assert res.status_code == 404


def test_customer_id_is_required(client):
    res = client.post("/api/v1/chat", json={"message": "hi"})
    assert res.status_code == 422


def test_missing_api_key_returns_503(client):
    res = client.post("/api/v1/chat", json={"message": "hi", "customer_id": "CUS-1001"})
    assert res.status_code == 503
    assert "GROQ_API_KEY" in res.json()["detail"]


def test_runaway_tool_loop_is_stopped_and_session_recovers(client, fake_llm):
    fake_llm.agent_replies = [tool_call("get_customer_details", call_id=f"c{i}") for i in range(50)]
    first = client.post("/api/v1/chat", json={"message": "billing question", "customer_id": "CUS-1001"})
    assert first.status_code == 200
    assert "couldn't finish" in first.json()["response"]

    fake_llm.agent_replies = [AIMessage(content="Back to normal.")]
    second = client.post("/api/v1/chat", json={"message": "hello again", "customer_id": "CUS-1001",
                                               "session_id": first.json()["session_id"]})
    assert second.status_code == 200
    assert second.json()["response"] == "Back to normal."


def test_chat_returns_sources_used(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("search_knowledge_base", {"query": "support hours"}),
        AIMessage(content="Humans work Mon-Fri 9:00-18:00 PKT [Company overview and support hours > Human support hours]."),
    ]
    data = client.post("/api/v1/chat", json={"message": "what are your support hours?",
                                             "customer_id": "CUS-1001"}).json()
    assert data["sources"] == ["Company overview and support hours > Human support hours"]


def test_sources_fall_back_to_everything_retrieved(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("search_knowledge_base", {"query": "support hours"}),
        AIMessage(content="Our team works weekdays."),   # no citation
    ]
    data = client.post("/api/v1/chat", json={"message": "when are you open?", "customer_id": "CUS-1001"}).json()
    assert len(data["sources"]) > 1


def test_sources_empty_when_no_search(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="Hello!")]
    data = client.post("/api/v1/chat", json={"message": "hi", "customer_id": "CUS-1001"}).json()
    assert data["sources"] == []


def test_health_reports_knowledge(client):
    assert "documents" in client.get("/health").json()["knowledge"]
