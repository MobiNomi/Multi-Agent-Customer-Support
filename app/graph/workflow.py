import inspect
import json
import logging
from typing import List, Literal, Optional, Tuple

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.graph.agents import AGENT_TOOLS, billing_agent_node, general_agent_node, technical_agent_node
from app.graph.history import last_human_index, latest_user_text
from app.graph.router import supervisor_router_node
from app.graph.state import SupportState
from app.guardrails.nodes import (_thread, _with_notes, input_guard_node, output_guard_node, refusal_node,
                                  route_after_input_guard)
from app.guardrails.notes import record
from app.textnorm import normalize_text
from app.tools.db_tools import create_support_ticket, open_ticket, recent_tickets

logger = logging.getLogger(__name__)

AGENT_NODES = ("billing_agent", "technical_agent", "general_agent")
AgentName = Literal["billing_agent", "technical_agent", "general_agent"]

TOOL_REGISTRY = {t.name: t for tools in AGENT_TOOLS.values() for t in tools}
# Arguments the executor fills in from the signed-in session, never from the model.
INJECTED_ARGS = ("customer_id", "thread_id")
TOOL_INJECTIONS = {name: [a for a in INJECTED_ARGS if a in inspect.signature(t.func).parameters]
                   for name, t in TOOL_REGISTRY.items()}
CUSTOMER_SCOPED = {name for name, args in TOOL_INJECTIONS.items() if "customer_id" in args}


def _call_key(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


def _clean_args(args: dict) -> dict:
    return {k: normalize_text(v).strip() if isinstance(v, str) else v for k, v in (args or {}).items()}


def ticket_guard(customer_id: str, category: str) -> Tuple[Optional[dict], List[str]]:
    """Action guardrail: reuse a recent ticket on the same topic, and cap new tickets per hour.

    Returns (result to give the agent instead of opening a ticket, guardrail kinds), or (None, []) to allow it.
    """
    settings = get_settings()
    if not settings.guardrails_enabled:
        return None, []
    recent = recent_tickets(customer_id, within_minutes=60)
    same = next((t for t in recent if t["category"] == category and t["status"] in ("Open", "Escalated")), None)
    if same:
        return {"status": "existing", "ticket_id": same["ticket_id"],
                "message": f"A {category} ticket ({same['ticket_id']}) was already opened in the last hour. "
                           "Give the customer this ticket ID instead of opening another."}, ["ticket_reused"]
    if len(recent) >= settings.max_tickets_per_hour:
        return {"error": "The limit for new tickets this hour has been reached. Tell the customer their "
                         f"existing ticket {recent[0]['ticket_id']} is with the team."}, ["ticket_limit"]
    return None, []


def tool_execution_node(state: SupportState, config: RunnableConfig = None) -> dict:
    """Run the tool calls in the last agent message and return the results as ToolMessages."""
    messages = state["messages"]
    last = messages[-1]
    calls = getattr(last, "tool_calls", None) or []
    agent = state.get("assigned_agent")
    allowed = {t.name for t in AGENT_TOOLS.get(agent, [])}

    # Calls already made while answering this message: repeating one is a loop, not new information.
    start = last_human_index(messages) or 0
    seen = {_call_key(c["name"], _clean_args(c.get("args")))
            for m in messages[start:-1] for c in (getattr(m, "tool_calls", None) or [])}

    results, ticket_id, guard_kinds = [], None, []
    for call in calls:
        name = call["name"]
        args = _clean_args(call.get("args"))
        key = _call_key(name, args)
        if name not in allowed:
            result = {"error": f"Tool '{name}' is not available to {agent}."}
        elif key in seen:
            result = {"note": "You already made this exact call while answering this message. "
                              "Use the earlier result instead of calling it again."}
        else:
            seen.add(key)
            blocked, kinds = (ticket_guard(state["customer_id"], args.get("category", "General"))
                              if name == create_support_ticket.name else (None, []))
            guard_kinds += kinds
            if blocked is not None:
                result = blocked
            else:
                injected = {"customer_id": state["customer_id"], "thread_id": _thread(config)}
                for arg in TOOL_INJECTIONS.get(name, []):
                    args[arg] = injected[arg]                   # overrides anything the model sent
                try:
                    result = TOOL_REGISTRY[name].invoke(args)
                except Exception as exc:
                    logger.exception("Tool %s failed", name)
                    result = {"error": f"{name} failed: {exc}"}

        if name == create_support_ticket.name and isinstance(result, dict) and result.get("ticket_id"):
            ticket_id = result["ticket_id"]
        results.append(ToolMessage(content=json.dumps(result, default=str),
                                   tool_call_id=call["id"], name=name))

    update: dict = {"messages": results}
    if ticket_id:
        update["ticket_id"] = ticket_id
    if guard_kinds:
        record(guard_kinds, state.get("customer_id"), _thread(config))
        update["guardrail_notes"] = _with_notes(state, guard_kinds)
    return update


def human_escalation_node(state: SupportState, config: RunnableConfig = None) -> dict:
    """Open an escalated ticket and tell the customer a human will take over."""
    category = {"billing": "Billing", "technical": "Technical"}.get(state.get("intent") or "", "General")
    if get_settings().guardrails_enabled:
        recent = recent_tickets(state["customer_id"], within_minutes=60)
        existing = next((t for t in recent if t["status"] == "Escalated"), None)
        kind = "ticket_reused" if existing else None
        if existing is None and len(recent) >= get_settings().max_tickets_per_hour:
            existing, kind = recent[0], "ticket_limit"
        if existing:
            record([kind], state.get("customer_id"), _thread(config))
            return {
                "messages": [AIMessage(content=(f"Your request is already with our human support team under ticket "
                                                f"{existing['ticket_id']}. A support specialist will follow up "
                                                f"with you."))],
                "assigned_agent": "human_escalation",
                "ticket_id": existing["ticket_id"],
                "resolution": "Escalation already open.",
                "guardrail_notes": _with_notes(state, [kind]),
            }
    try:
        ticket_id = open_ticket(state["customer_id"], category,
                                description=latest_user_text(state.get("messages", [])) or "Escalation requested",
                                priority="High", status="Escalated", thread_id=_thread(config))
    except Exception:
        logger.exception("Could not open escalation ticket")
        return {
            "messages": [AIMessage(content=("I'm handing this to our human support team. Something went "
                                            "wrong while creating your ticket, so please contact support "
                                            "directly if you don't hear back soon."))],
            "assigned_agent": "human_escalation",
            "resolution": "Escalation requested; ticket creation failed.",
        }
    return {
        "messages": [AIMessage(content=(f"I've passed this to our human support team. Your ticket number is "
                                        f"{ticket_id}, and a support specialist will follow up with you."))],
        "assigned_agent": "human_escalation",
        "ticket_id": ticket_id,
        "resolution": "Escalated to human support operator.",
    }


def route_supervisor(state: SupportState) -> Literal["billing_agent", "technical_agent", "general_agent",
                                                      "human_escalation", "refusal"]:
    if state.get("needs_human"):
        return "human_escalation"
    agent = state.get("assigned_agent")
    if agent == "refusal":
        return "refusal"
    return agent if agent in AGENT_NODES else "general_agent"


def should_continue_tools(state: SupportState) -> Literal["tool_executor", "output_guard"]:
    last = state["messages"][-1]
    return "tool_executor" if getattr(last, "tool_calls", None) else "output_guard"


def route_back_to_agent(state: SupportState) -> AgentName:
    """Send tool results back to the agent that asked for them."""
    agent = state.get("assigned_agent")
    return agent if agent in AGENT_NODES else "general_agent"


def build_support_graph(checkpointer=None):
    """Build and compile the support graph. Pass a checkpointer to keep conversation memory."""
    builder = StateGraph(SupportState)

    builder.add_node("input_guard", input_guard_node)
    builder.add_node("supervisor", supervisor_router_node)
    builder.add_node("billing_agent", billing_agent_node)
    builder.add_node("technical_agent", technical_agent_node)
    builder.add_node("general_agent", general_agent_node)
    builder.add_node("tool_executor", tool_execution_node)
    builder.add_node("human_escalation", human_escalation_node)
    builder.add_node("refusal", refusal_node)
    builder.add_node("output_guard", output_guard_node)

    builder.add_edge(START, "input_guard")
    builder.add_conditional_edges("input_guard", route_after_input_guard,
                                  {"supervisor": "supervisor", "end": END})
    builder.add_conditional_edges("supervisor", route_supervisor, {
        "billing_agent": "billing_agent",
        "technical_agent": "technical_agent",
        "general_agent": "general_agent",
        "human_escalation": "human_escalation",
        "refusal": "refusal",
    })
    for agent in AGENT_NODES:
        builder.add_conditional_edges(agent, should_continue_tools,
                                      {"tool_executor": "tool_executor", "output_guard": "output_guard"})
    builder.add_conditional_edges("tool_executor", route_back_to_agent,
                                  {agent: agent for agent in AGENT_NODES})
    builder.add_edge("output_guard", END)
    builder.add_edge("human_escalation", END)
    builder.add_edge("refusal", END)

    return builder.compile(checkpointer=checkpointer)
