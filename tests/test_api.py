import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from app.main import app
from tests.fakes import tool_call
from tests.helpers import SARA, sign_in, switch_to


@pytest.fixture
def anon():
    with TestClient(app) as c:  # context manager runs the lifespan (init_db + graph)
        yield c


@pytest.fixture
def client(anon):
    """Signed in as Ali (CUS-1001)."""
    sign_in(anon)
    return anon


def test_health_check(anon):
    client = anon
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
    res = client.post("/api/v1/chat", json={"message": "I was charged twice for order ORD-1034"})
    assert res.status_code == 200
    data = res.json()
    assert data["assigned_agent"] == "billing_agent"
    assert data["intent"] == "billing"
    assert data["ticket_id"].startswith("TCK-")
    assert "ORD-1034" in data["response"]
    assert data["session_id"]


def test_session_continues_conversation(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="Hi Ali!"), AIMessage(content="Still here.")]
    first = client.post("/api/v1/chat", json={"message": "hello"}).json()
    second = client.post("/api/v1/chat", json={"message": "are you there?",
                                               "session_id": first["session_id"]}).json()
    assert second["session_id"] == first["session_id"]
    assert second["response"] == "Still here."
    # The second model call received the first exchange as history.
    contents = [m.content for m in fake_llm.calls[-1]]
    assert "hello" in contents and "Hi Ali!" in contents


def test_same_session_id_different_customer_is_separate(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="one"), AIMessage(content="two")]
    client.post("/api/v1/chat", json={"message": "secret", "session_id": "s1"})
    switch_to(client, SARA)
    client.post("/api/v1/chat", json={"message": "hi", "session_id": "s1"})
    assert "secret" not in [m.content for m in fake_llm.calls[-1]]


def test_ticket_id_resets_each_turn(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="ok")]
    first = client.post("/api/v1/chat", json={"message": "I need a human"}).json()
    assert first["ticket_id"]
    second = client.post("/api/v1/chat", json={"message": "thanks",
                                               "session_id": first["session_id"]}).json()
    assert second["ticket_id"] is None and second["needs_human"] is False


def test_chat_requires_sign_in(anon, fake_llm):
    res = anon.post("/api/v1/chat", json={"message": "hi"})
    assert res.status_code == 401
    assert fake_llm.calls == []


def test_customer_id_in_body_is_ignored(client, fake_llm):
    # Signed in as Ali, pretending to be Sara: the request still runs as Ali.
    fake_llm.agent_replies = [tool_call("get_customer_details"), AIMessage(content="ok")]
    data = client.post("/api/v1/chat", json={"message": "billing: my account", "customer_id": "CUS-1002"}).json()
    state = app.state.graph.get_state({"configurable": {"thread_id": f"CUS-1001:{data['session_id']}"}}).values
    tool_result = next(m for m in state["messages"] if isinstance(m, ToolMessage)).content
    assert "Ali Khan" in tool_result and "Sara" not in tool_result


def test_message_is_required(client):
    assert client.post("/api/v1/chat", json={}).status_code == 422


def test_missing_api_key_returns_503(client):
    res = client.post("/api/v1/chat", json={"message": "hi"})
    assert res.status_code == 503
    assert "GROQ_API_KEY" in res.json()["detail"]


def test_runaway_tool_loop_is_stopped_and_session_recovers(client, fake_llm):
    fake_llm.agent_replies = [tool_call("get_customer_details", call_id=f"c{i}") for i in range(50)]
    first = client.post("/api/v1/chat", json={"message": "billing question"})
    assert first.status_code == 200
    assert "couldn't finish" in first.json()["response"]

    fake_llm.agent_replies = [AIMessage(content="Back to normal.")]
    second = client.post("/api/v1/chat", json={"message": "hello again",
                                               "session_id": first.json()["session_id"]})
    assert second.status_code == 200
    assert second.json()["response"] == "Back to normal."


def test_chat_returns_sources_used(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("search_knowledge_base", {"query": "support hours"}),
        AIMessage(content="Humans work Mon-Fri 9:00-18:00 PKT [Company overview and support hours > Human support hours]."),
    ]
    data = client.post("/api/v1/chat", json={"message": "what are your support hours?"}).json()
    assert data["sources"] == ["Company overview and support hours > Human support hours"]


def test_retrieved_lists_everything_looked_up(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("search_knowledge_base", {"query": "support hours"}),
        AIMessage(content="Our team works weekdays."),   # no citation
    ]
    data = client.post("/api/v1/chat", json={"message": "when are you open?"}).json()
    assert data["sources"] == [] and len(data["retrieved"]) > 1


def test_sources_empty_when_no_search(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="Hello!")]
    data = client.post("/api/v1/chat", json={"message": "hi"}).json()
    assert data["sources"] == []


def test_health_reports_knowledge(client):
    assert "documents" in client.get("/health").json()["knowledge"]


# ---------- chat page support ----------

def test_chat_page_is_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "AcmeCloud Support" in res.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/fonts/instrument-sans-latin-400-normal.woff2").status_code == 200


def test_demo_accounts(anon):
    data = anon.get("/api/v1/demo/accounts").json()
    assert {"name": "Ali Khan", "email": "ali@example.com", "plan": "Pro", "password": "demo1234",
            "role": "customer"} in data
    assert {"name": "Hina Raza", "email": "staff@acmecloud.example", "plan": "Staff", "password": "demo1234",
            "role": "staff"} in data


def test_demo_accounts_disabled(anon, monkeypatch):
    from app import config
    monkeypatch.setenv("DEMO_MODE", "false")
    config.get_settings.cache_clear()
    assert anon.get("/api/v1/demo/accounts").status_code == 404


def test_chat_reports_tools_used(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD-1034"}),
        AIMessage(content="TXN-8922 is a duplicate."),
    ]
    data = client.post("/api/v1/chat", json={"message": "charged twice for ORD-1034"}).json()
    assert data["tools_used"] == [{"name": "get_order_payments", "args": {"order_id": "ORD-1034"}}]


def test_session_history(client, fake_llm):
    fake_llm.agent_replies = [
        AIMessage(content="Let me check.", tool_calls=[{"name": "get_customer_details", "args": {}, "id": "c1"}]),
        AIMessage(content="You're on Pro."),
    ]
    sid = client.post("/api/v1/chat", json={"message": "what plan am I on? billing"}).json()["session_id"]
    hist = client.get(f"/api/v1/sessions/{sid}/messages").json()
    # tool traffic and the "let me check" text that came with a tool call are left out
    assert hist["messages"] == [{"role": "customer", "content": "what plan am I on? billing"},
                                {"role": "assistant", "content": "You're on Pro."}]


def test_session_history_is_per_customer(client, fake_llm):
    fake_llm.agent_replies = [AIMessage(content="hi Ali")]
    sid = client.post("/api/v1/chat", json={"message": "hello"}).json()["session_id"]
    switch_to(client, SARA)
    other = client.get(f"/api/v1/sessions/{sid}/messages").json()
    assert other["messages"] == []
    client.post("/api/v1/auth/logout")
    assert client.get(f"/api/v1/sessions/{sid}/messages").status_code == 401


def test_reply_lookalike_characters_are_cleaned(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("search_knowledge_base", {"query": "support hours"}),
        AIMessage(content="Order ORD‑1034 and TXN‑8922 "
                          "[Company overview and support hours > Human support hours]."),
    ]
    data = client.post("/api/v1/chat", json={"message": "hours?"}).json()
    assert "ORD-1034" in data["response"] and "TXN-8922" in data["response"]
    assert "Order ORD-1034" in data["response"]
    assert data["sources"] == ["Company overview and support hours > Human support hours"]
