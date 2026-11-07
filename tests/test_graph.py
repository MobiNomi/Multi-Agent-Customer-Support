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


def test_general_agent_answers_from_knowledge_base(fake_llm):
    fake_llm.agent_replies = [
        tool_call("search_knowledge_base", {"query": "refund annual plan"}),
        AIMessage(content="Annual plans can be refunded within 14 days [Refund policy > Annual plans]."),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, nodes = run(graph, "what is your policy on annual plans?", thread="kb1")

    assert nodes == ["supervisor", "general_agent", "tool_executor", "general_agent"]
    result = json.loads(next(m for m in state["messages"] if isinstance(m, ToolMessage)).content)
    assert result["results"][0]["source"] == "Refund policy > Annual plans"
    assert "search_knowledge_base" in fake_llm.bound[0]


# ---------- loop protection and messy model output ----------

def test_tool_rounds_are_capped_then_agent_must_answer(fake_llm):
    # A model that never stops searching.
    fake_llm.agent_replies = [tool_call("search_knowledge_base", {"query": f"refund {i}"}, call_id=f"c{i}")
                              for i in range(10)]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, nodes = run(graph, "refund question for billing", thread="cap")

    assert nodes.count("tool_executor") == 4                      # MAX_TOOL_ROUNDS default
    assert fake_llm.tool_choices[-1] == "none"                    # last call: tools switched off
    assert "tool limit" in fake_llm.calls[-1][0].content          # and told to answer now
    assert "couldn't finish" in state["messages"][-1].content     # ignored that, so calls were dropped
    assert not state["messages"][-1].tool_calls


def test_forced_answer_uses_model_text_when_given(fake_llm):
    fake_llm.agent_replies = [tool_call("search_knowledge_base", {"query": f"q{i}"}, call_id=f"c{i}")
                              for i in range(4)] + [AIMessage(content="Annual plans: 14 days.")]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, _ = run(graph, "billing refund", thread="cap2")
    assert state["messages"][-1].content == "Annual plans: 14 days."


def test_duplicate_tool_call_is_not_run_twice(fake_llm):
    fake_llm.agent_replies = [
        tool_call("search_knowledge_base", {"query": "refund annual"}, call_id="a"),
        tool_call("search_knowledge_base", {"query": "refund annual"}, call_id="b"),
        AIMessage(content="done"),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, _ = run(graph, "billing refund", thread="dup")
    tool_msgs = [json.loads(m.content) for m in state["messages"] if isinstance(m, ToolMessage)]
    assert "results" in tool_msgs[0]
    assert "already made this exact call" in tool_msgs[1]["note"]


def test_lookalike_hyphen_in_tool_args_still_finds_order(fake_llm):
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD‑1034"}),   # non-breaking hyphen
        AIMessage(content="ok"),
    ]
    graph = build_support_graph(checkpointer=InMemorySaver())
    state, _ = run(graph, "charged twice", thread="nbh")
    result = json.loads(next(m for m in state["messages"] if isinstance(m, ToolMessage)).content)
    assert result["payment_records_count"] == 2
