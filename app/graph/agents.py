from langchain_core.messages import SystemMessage

from app.graph.history import history_for_llm
from app.graph.state import SupportState
from app.llm import get_llm
from app.tools.db_tools import create_support_ticket, get_customer_details, get_order_payments

# Which tools each specialist may call. The tool executor enforces this list too.
AGENT_TOOLS = {
    "billing_agent": [get_customer_details, get_order_payments, create_support_ticket],
    "technical_agent": [get_customer_details, create_support_ticket],
    "general_agent": [],
}

COMMON_RULES = (
    "Keep replies short, friendly and specific. Every tool already acts on the current customer's "
    "own account; you cannot look up other customers. Never invent order, payment or ticket details: "
    "if a tool returns an error, tell the customer plainly and ask them to check the ID."
)

PROMPTS = {
    "billing_agent": (
        "You are the Billing Support Agent. You handle charges, duplicate charges, refunds and invoices.\n"
        "Check the records with your tools before stating any fact about a payment. If the customer gives "
        "an order ID, call get_order_payments for it.\n"
        "If a duplicate charge is confirmed, name the duplicate transaction ID and open a Billing ticket "
        "for the refund. You cannot issue refunds yourself: say a refund request has been opened and give "
        "the ticket ID. Never say the money has already been returned."
    ),
    "technical_agent": (
        "You are the Technical Support Specialist. You handle bugs, API errors, configuration, login and "
        "performance problems.\n"
        "Give concise, numbered troubleshooting steps. If the problem needs engineering (an outage, data "
        "loss, a reproducible bug) or the steps did not help, open a Technical ticket and give the ticket ID."
    ),
    "general_agent": (
        "You are the General Customer Support Agent. You answer questions about the service and plans and "
        "handle greetings.\n"
        "You do not have a knowledge base yet, so do not invent prices, opening hours or policies. If you "
        "do not know something, say so and offer to connect the customer with a human agent."
    ),
}


def _run_agent(agent_name: str, state: SupportState) -> dict:
    llm = get_llm()
    tools = AGENT_TOOLS[agent_name]
    if tools:
        llm = llm.bind_tools(tools)

    system = SystemMessage(content=(
        f"{PROMPTS[agent_name]}\n\n{COMMON_RULES}\n"
        f"Current customer ID: {state.get('customer_id', 'unknown')}."
    ))
    response = llm.invoke([system] + history_for_llm(state.get("messages", [])))
    # Return only what changed; the add_messages reducer appends the reply.
    return {"messages": [response], "assigned_agent": agent_name}


def billing_agent_node(state: SupportState) -> dict:
    """Specialist for payments, refunds and invoices."""
    return _run_agent("billing_agent", state)


def technical_agent_node(state: SupportState) -> dict:
    """Specialist for platform, API and account errors."""
    return _run_agent("technical_agent", state)


def general_agent_node(state: SupportState) -> dict:
    """Generalist for everything else."""
    return _run_agent("general_agent", state)
