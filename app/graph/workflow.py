import inspect
import json
import logging
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.graph.agents import AGENT_TOOLS, billing_agent_node, general_agent_node, technical_agent_node
from app.graph.history import last_human_index, latest_user_text
from app.graph.router import supervisor_router_node
from app.graph.state import SupportState
from app.textnorm import normalize_text
from app.tools.db_tools import create_support_ticket, open_ticket

logger = logging.getLogger(__name__)

AGENT_NODES = ("billing_agent", "technical_agent", "general_agent")
AgentName = Literal["billing_agent", "technical_agent", "general_agent"]

TOOL_REGISTRY = {t.name: t for tools in AGENT_TOOLS.values() for t in tools}
# Tools that take the customer's ID; the executor injects it from the session.
CUSTOMER_SCOPED = {name for name, t in TOOL_REGISTRY.items()
                   if "customer_id" in inspect.signature(t.func).parameters}


def _call_key(name: str, args: dict) -> str:
    return f"{name}:{json.dumps(args, sort_keys=True, default=str)}"


def _clean_args(args: dict) -> dict:
    return {k: normalize_text(v).strip() if isinstance(v, str) else v for k, v in (args or {}).items()}


def tool_execution_node(state: SupportState) -> dict:
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

    results, ticket_id = [], None
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
            if name in CUSTOMER_SCOPED:
                args["customer_id"] = state["customer_id"]  # overrides anything the model sent
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
    return update


def human_escalation_node(state: SupportState) -> dict:
    """Open an escalated ticket and tell the customer a human will take over."""
    category = {"billing": "Billing", "technical": "Technical"}.get(state.get("intent") or "", "General")
    try:
        ticket_id = open_ticket(state["customer_id"], category,
                                description=latest_user_text(state.get("messages", [])) or "Escalation requested",
                                priority="High", status="Escalated")
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


def route_supervisor(state: SupportState) -> Literal["billing_agent", "technical_agent",
                                                      "general_agent", "human_escalation"]:
    if state.get("needs_human"):
        return "human_escalation"
    agent = state.get("assigned_agent")
    return agent if agent in AGENT_NODES else "general_agent"


def should_continue_tools(state: SupportState) -> Literal["tool_executor", "end"]:
    last = state["messages"][-1]
    return "tool_executor" if getattr(last, "tool_calls", None) else "end"


def route_back_to_agent(state: SupportState) -> AgentName:
    """Send tool results back to the agent that asked for them."""
    agent = state.get("assigned_agent")
    return agent if agent in AGENT_NODES else "general_agent"


def build_support_graph(checkpointer=None):
    """Build and compile the support graph. Pass a checkpointer to keep conversation memory."""
    builder = StateGraph(SupportState)

    builder.add_node("supervisor", supervisor_router_node)
    builder.add_node("billing_agent", billing_agent_node)
    builder.add_node("technical_agent", technical_agent_node)
    builder.add_node("general_agent", general_agent_node)
    builder.add_node("tool_executor", tool_execution_node)
    builder.add_node("human_escalation", human_escalation_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_supervisor, {
        "billing_agent": "billing_agent",
        "technical_agent": "technical_agent",
        "general_agent": "general_agent",
        "human_escalation": "human_escalation",
    })
    for agent in AGENT_NODES:
        builder.add_conditional_edges(agent, should_continue_tools,
                                      {"tool_executor": "tool_executor", "end": END})
    builder.add_conditional_edges("tool_executor", route_back_to_agent,
                                  {agent: agent for agent in AGENT_NODES})
    builder.add_edge("human_escalation", END)

    return builder.compile(checkpointer=checkpointer)
