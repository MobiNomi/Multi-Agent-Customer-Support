from langchain_core.messages import SystemMessage

from app.graph.history import history_for_llm
from app.graph.state import SupportState
from app.llm import get_llm
from app.tools.db_tools import create_support_ticket, get_customer_details, get_order_payments
from app.tools.knowledge_tools import search_knowledge_base

# Which tools each specialist may call. The tool executor enforces this list too.
AGENT_TOOLS = {
    "billing_agent": [search_knowledge_base, get_customer_details, get_order_payments, create_support_ticket],
    "technical_agent": [search_knowledge_base, get_customer_details, create_support_ticket],
    "general_agent": [search_knowledge_base],
}

COMMON_RULES = (
    "Rules for every reply:\n"
    "- Company facts (prices, plans, policies, timelines, how-to steps, error codes) must come from "
    "search_knowledge_base. Search before answering, and answer only from what it returns. If the results "
    "don't cover the question, say you don't have that information and offer a human agent. Never fill gaps "
    "from general knowledge.\n"
    "- After each fact from the documents, cite its source in square brackets exactly as given, "
    "e.g. [Refund policy > Annual plans].\n"
    "- Customer facts (orders, payments, tickets) must come from the account tools. Every account tool "
    "already acts on the current customer's own account; you cannot look up other customers. If a tool "
    "returns an error, tell the customer plainly and ask them to check the ID.\n"
    "- Keep replies short, friendly and specific."
)

PROMPTS = {
    "billing_agent": (
        "You are the Billing Support Agent for AcmeCloud. You handle charges, duplicate charges, refunds, "
        "invoices, plan changes and orders.\n"
        "Check the customer's records with the account tools before stating any fact about their payments. "
        "If they give an order ID, call get_order_payments for it. Look up the relevant policy with "
        "search_knowledge_base so you can tell them what happens next and how long it takes.\n"
        "If a duplicate charge is confirmed, name the duplicate transaction ID and open a Billing ticket for "
        "the refund. You cannot issue refunds yourself: say a refund request has been opened and give the "
        "ticket ID. Never say the money has already been returned."
    ),
    "technical_agent": (
        "You are the Technical Support Specialist for AcmeCloud. You handle bugs, API errors, login, sync, "
        "performance and automation problems.\n"
        "Search the knowledge base for the matching troubleshooting guide or error code and give its steps as "
        "a short numbered list. If the problem needs engineering (an outage, data loss, a repeating error) or "
        "the documented steps did not help, open a Technical ticket and give the ticket ID."
    ),
    "general_agent": (
        "You are the General Customer Support Agent for AcmeCloud. You answer questions about the company, "
        "plans, features, accounts, security and privacy, and you handle greetings and small talk.\n"
        "Greetings and thanks need no search. For anything about AcmeCloud, search first."
    ),
}


def _run_agent(agent_name: str, state: SupportState) -> dict:
    llm = get_llm().bind_tools(AGENT_TOOLS[agent_name])
    system = SystemMessage(content=(
        f"{PROMPTS[agent_name]}\n\n{COMMON_RULES}\n"
        f"Current customer ID: {state.get('customer_id', 'unknown')}."
    ))
    response = llm.invoke([system] + history_for_llm(state.get("messages", [])))
    # Return only what changed; the add_messages reducer appends the reply.
    return {"messages": [response], "assigned_agent": agent_name}


def billing_agent_node(state: SupportState) -> dict:
    """Specialist for payments, refunds, invoices and orders."""
    return _run_agent("billing_agent", state)


def technical_agent_node(state: SupportState) -> dict:
    """Specialist for platform, API and account errors."""
    return _run_agent("technical_agent", state)


def general_agent_node(state: SupportState) -> dict:
    """Generalist for everything else."""
    return _run_agent("general_agent", state)
