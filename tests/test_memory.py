import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from app import config
from app.graph.workflow import build_support_graph
from app.main import app
from app.memory import count_conversations, open_checkpointer


def turn(text, customer="CUS-1001"):
    return {"messages": [HumanMessage(content=text)], "customer_id": customer,
            "ticket_id": None, "needs_human": False}


def test_default_backend_is_sqlite_file(tmp_path):
    with open_checkpointer() as saver:
        assert isinstance(saver, SqliteSaver)
    assert (tmp_path / "checkpoints.db").exists()


def test_conversation_survives_restart(fake_llm):
    fake_llm.agent_replies = [AIMessage(content="Hi Ali, how can I help?"), AIMessage(content="Still here.")]
    cfg = {"configurable": {"thread_id": "CUS-1001:abc"}}

    with open_checkpointer() as saver:          # first "server run"
        build_support_graph(checkpointer=saver).invoke(turn("hello"), cfg)

    with open_checkpointer() as saver:          # brand new process, same file
        graph = build_support_graph(checkpointer=saver)
        saved = graph.get_state(cfg).values["messages"]
        assert [m.content for m in saved] == ["hello", "Hi Ali, how can I help?"]

        graph.invoke(turn("are you there?"), cfg)
        sent_to_model = [m.content for m in fake_llm.calls[-1]]
        assert "hello" in sent_to_model and "Hi Ali, how can I help?" in sent_to_model


def test_api_session_survives_server_restart(fake_llm):
    fake_llm.agent_replies = [AIMessage(content="Noted: ORD-1034."), AIMessage(content="Yes, ORD-1034.")]

    with TestClient(app) as client:              # server start #1
        first = client.post("/api/v1/chat", json={"message": "my order is ORD-1034",
                                                  "customer_id": "CUS-1001"}).json()

    with TestClient(app) as client:              # server restart
        second = client.post("/api/v1/chat", json={"message": "which order did I mention?",
                                                   "customer_id": "CUS-1001",
                                                   "session_id": first["session_id"]}).json()

    assert second["response"] == "Yes, ORD-1034."
    assert "my order is ORD-1034" in [m.content for m in fake_llm.calls[-1]]


def test_memory_backend_forgets_on_restart(monkeypatch, fake_llm):
    monkeypatch.setenv("MEMORY_BACKEND", "memory")
    config.get_settings.cache_clear()
    fake_llm.agent_replies = [AIMessage(content="one"), AIMessage(content="two")]
    cfg = {"configurable": {"thread_id": "CUS-1001:ram"}}

    with open_checkpointer() as saver:
        assert isinstance(saver, InMemorySaver)
        build_support_graph(checkpointer=saver).invoke(turn("hello"), cfg)
    with open_checkpointer() as saver:
        assert build_support_graph(checkpointer=saver).get_state(cfg).values == {}


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("MEMORY_BACKEND", "redis")
    config.get_settings.cache_clear()
    with pytest.raises(ValueError, match="MEMORY_BACKEND"):
        with open_checkpointer():
            pass


def test_count_conversations(fake_llm):
    fake_llm.agent_replies = [AIMessage(content="a"), AIMessage(content="b"), AIMessage(content="c")]
    with open_checkpointer() as saver:
        graph = build_support_graph(checkpointer=saver)
        assert count_conversations(saver) == 0
        graph.invoke(turn("hi"), {"configurable": {"thread_id": "CUS-1001:s1"}})
        graph.invoke(turn("hi again"), {"configurable": {"thread_id": "CUS-1001:s1"}})
        graph.invoke(turn("hello"), {"configurable": {"thread_id": "CUS-1002:s2"}})
        assert count_conversations(saver) == 2


def test_health_reports_memory_backend():
    with TestClient(app) as client:
        assert client.get("/health").json()["memory"] == "sqlite"
