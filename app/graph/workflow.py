import json
from typing import Literal
from langchain_core.messages import ToolMessage
from langgraph.graph import StateGraph, START, END

from app.graph.state import SupportState
from app.graph.router import supervisor_router_node
from app.graph.agents import billing_agent_node, technical_agent_node, general_agent_node
from app.tools.db_tools import get_customer_details, get_order_payments, create_support_ticket

# Map tools for execution
TOOLS = {
    "get_customer_details": get_customer_details,
    "get_order_payments": get_order_payments,
    "create_support_ticket": create_support_ticket,
}


def tool_execution_node(state: SupportState) -> SupportState:
    """Executes tool calls requested by an agent and appends results back to messages."""
    last_message = state["messages"][-1]
    
    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        return state

    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        tool_call_id = tool_call["id"]

        if tool_name in TOOLS:
            result = TOOLS[tool_name].invoke(tool_args)
        else:
            result = {"error": f"Tool '{tool_name}' not found."}

        # Append result back as a ToolMessage so the agent can generate a final response
        state["messages"].append(
            ToolMessage(
                content=json.dumps(result),
                tool_call_id=tool_call_id,
                name=tool_name
            )
        )

    return state


def route_supervisor(state: SupportState) -> Literal["billing_agent", "technical_agent", "general_agent", "human_escalation"]:
    """Determines which agent node to invoke based on supervisor router decision."""
    if state.get("needs_human"):
        return "human_escalation"
    
    agent = state.get("assigned_agent", "general_agent")
    if agent in ["billing_agent", "technical_agent", "general_agent"]:
        return agent
    return "general_agent"


def should_continue_tools(state: SupportState) -> Literal["tool_executor", "end_node"]:
    """Checks if the last agent message contains pending tool calls."""
    last_message = state["messages"][-1]
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tool_executor"
    return "end_node"


def human_escalation_node(state: SupportState) -> SupportState:
    """Fallback node when query requires human operator intervention."""
    state["resolution"] = "Escalated to human support operator."
    return state


def build_support_graph():
    """Constructs and compiles the full multi-agent support graph."""
    builder = StateGraph(SupportState)

    # 1. Add Nodes
    builder.add_node("supervisor", supervisor_router_node)
    builder.add_node("billing_agent", billing_agent_node)
    builder.add_node("technical_agent", technical_agent_node)
    builder.add_node("general_agent", general_agent_node)
    builder.add_node("tool_executor", tool_execution_node)
    builder.add_node("human_escalation", human_escalation_node)

    # 2. Add Edges
    builder.add_edge(START, "supervisor")

    # Supervisor conditional routing to agents
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "billing_agent": "billing_agent",
            "technical_agent": "technical_agent",
            "general_agent": "general_agent",
            "human_escalation": "human_escalation",
        }
    )

    # Agent conditional routing (Tool Execution loop or End)
    for agent in ["billing_agent", "technical_agent", "general_agent"]:
        builder.add_conditional_edges(
            agent,
            should_continue_tools,
            {
                "tool_executor": "tool_executor",
                "end_node": END
            }
        )

    # After tool execution, loop back to the agent to form final response
    builder.add_edge("tool_executor", "billing_agent")
    builder.add_edge("human_escalation", END)

    return builder.compile()