import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.database.connection import session_scope
from app.database.models import SupportTicket
from app.graph.workflow import build_support_graph
from tests.fakes import tool_call


def run(graph, text, customer="CUS-1001", thread=None):
    """Invoke one turn and return (final_state, [node names in order])."""
    config = {"configurable": {"thread_id": thread}} if thread else {}
    nodes = []
    for update in graph.stream({"messages": [HumanMessage(content=text)], "customer_id": customer,
                                "ticket_id": None, "needs_human": False},
                               config, stream_mode="updates"):
        nodes.extend(update.keys())
    state = graph.get_state(config).values if thread else None
    return state, nodes


def test_billing_flow_uses_tools_and_answers(fake_llm):
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD-1034"}),
        AIMessage(content="You were charged twice. TXN-8922 is the duplicate."),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, nodes = run(graph, "I was charged twice for ORD-1034", thread="t1")

    assert nodes == ["supervisor", "billing_agent", "tool_executor", "billing_agent"]
    tool_msg = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert json.loads(tool_msg.content)["payment_records_count"] == 2
    assert state["assigned_agent"] == "billing_agent"
    assert state["messages"][-1].content.endswith("TXN-8922 is the duplicate.")


def test_tool_results_return_to_the_agent_that_called_them(fake_llm):
    # Regression: tool_executor used to be hard-wired back to billing_agent.
    fake_llm.agent_replies = [
        tool_call("get_customer_details"),
        AIMessage(content="Your Pro plan includes API access. Try regenerating your key."),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, nodes = run(graph, "I get an API error 401", thread="t2")

    assert nodes == ["supervisor", "technical_agent", "tool_executor", "technical_agent"]
    assert state["assigned_agent"] == "technical_agent"


def test_model_cannot_switch_customer(fake_llm):
    fake_llm.agent_replies = [
        tool_call("get_customer_details", {"customer_id": "CUS-1002"}),  # tries to read Sara's account
        AIMessage(content="done"),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, _ = run(graph, "billing question about my account", thread="t3")

    result = json.loads(next(m for m in state["messages"] if isinstance(m, ToolMessage)).content)
    assert result["customer_id"] == "CUS-1001"


def test_agent_cannot_use_tools_outside_its_list(fake_llm):
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD-1034"}),  # technical agent doesn't have this
        AIMessage(content="ok"),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, _ = run(graph, "login bug", thread="t4")

    result = json.loads(next(m for m in state["messages"] if isinstance(m, ToolMessage)).content)
    assert "not available" in result["error"]


def test_ticket_id_is_captured_in_state(fake_llm):
    fake_llm.agent_replies = [
        tool_call("create_support_ticket", {"category": "Billing", "description": "Refund TXN-8922",
                                            "priority": "High"}),
        AIMessage(content="Refund request opened."),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, _ = run(graph, "please refund the duplicate charge", thread="t5")

    assert state["ticket_id"].startswith("TCK-")
    with session_scope() as s:
        assert s.get(SupportTicket, state["ticket_id"]).customer_id == "CUS-1001"


def test_escalation_opens_ticket_and_replies(fake_llm):
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, nodes = run(graph, "I want to talk to a human right now", thread="t6")

    assert nodes == ["supervisor", "human_escalation"]
    assert state["needs_human"] is True
    assert state["ticket_id"] in state["messages"][-1].content
    with session_scope() as s:
        assert s.get(SupportTicket, state["ticket_id"]).status == "Escalated"
    assert fake_llm.calls == []  # no agent model call needed


def test_memory_across_turns(fake_llm):
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD-1034"}),
        AIMessage(content="TXN-8922 is a duplicate. Want me to open a refund request?"),
        AIMessage(content="Thanks for confirming."),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    run(graph, "I was charged twice for ORD-1034", thread="t7")
    state, _ = run(graph, "yes, refund billing please", thread="t7")

    humans = [m for m in state["messages"] if isinstance(m, HumanMessage)]
    assert len(humans) == 2
    # Router saw the earlier turn as context.
    assert "TXN-8922 is a duplicate" in fake_llm.router_inputs[-1]
    # Old tool traffic is not re-sent to the model on the next turn.
    last_call = fake_llm.calls[-1]
    assert not any(isinstance(m, ToolMessage) for m in last_call)
    assert any(isinstance(m, AIMessage) and "TXN-8922" in m.content for m in last_call)


def test_threads_are_isolated(fake_llm):
    fake_llm.agent_replies = [AIMessage(content="hello A"), AIMessage(content="hello B")]
    graph = build_support_graph(checkpointer=InMemorySaver())
    run(graph, "hi", thread="A")
    state_b, _ = run(graph, "hi there", thread="B")
    assert [m.content for m in state_b["messages"]] == ["hi there", "hello B"]
