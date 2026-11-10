from langchain_core.messages import AIMessage, SystemMessage

from app.config import get_settings
from app.graph.history import current_turn_tool_rounds, history_for_llm
from app.graph.state import SupportState
from app.llm import get_llm
from app.tools.action_tools import cancel_order, get_my_requests, request_refund, update_email
from app.tools.db_tools import create_support_ticket, get_customer_details, get_order_payments
from app.tools.knowledge_tools import search_knowledge_base

# Which tools each specialist may call. The tool executor enforces this list too.
# request_refund, update_email and cancel_order only *propose*: the customer confirms with a button.
AGENT_TOOLS = {
    "billing_agent": [search_knowledge_base, get_customer_details, get_order_payments, request_refund,
                      cancel_order, get_my_requests, create_support_ticket],
    "technical_agent": [search_knowledge_base, get_customer_details, update_email, create_support_ticket],
    "general_agent": [search_knowledge_base, update_email, get_my_requests],
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
    "- Account changes (refunds, cancellations, a new sign-in email) are only prepared by your tools; "
    "follow the instructions in the tool result. A change is done only when a later account update in the "
    "conversation says so.\n"
    "- Keep replies short, friendly and specific."
)

PROMPTS = {
    "billing_agent": (
        "You are the Billing Support Agent for AcmeCloud. You handle charges, duplicate charges, refunds, "
        "invoices, plan changes and orders.\n"
        "Check the customer's records with the account tools before stating any fact about their payments. "
        "If they give an order ID, call get_order_payments for it. Look up the relevant policy with "
        "search_knowledge_base so you can tell them what happens next and how long it takes.\n"
        "For general policy questions that name no order or payment (for example 'can I get a refund on an "
        "annual plan?'), one knowledge base search is enough; don't look up account records.\n"
        "If a duplicate charge is confirmed, name the duplicate transaction ID and call request_refund with it. "
        "If the customer wants to cancel an order, call cancel_order. To check on earlier requests, call "
        "get_my_requests. Open a Billing ticket only for problems these tools can't handle. You never move "
        "money yourself, so never say money has been returned."
    ),
    "technical_agent": (
        "You are the Technical Support Specialist for AcmeCloud. You handle bugs, API errors, login, sync, "
        "performance and automation problems.\n"
        "Search the knowledge base for the matching troubleshooting guide or error code and give its steps as "
        "a short numbered list. If the problem needs engineering (an outage, data loss, a repeating error) or "
        "the documented steps did not help, open a Technical ticket and give the ticket ID. If the customer "
        "wants to sign in with a different email address, call update_email."
    ),
    "general_agent": (
        "You are the General Customer Support Agent for AcmeCloud. You answer questions about the company, "
        "plans, features, accounts, security and privacy, and you handle greetings and small talk.\n"
        "Greetings and thanks need no search. For anything about AcmeCloud, search first. If the customer "
        "wants to change the email address they sign in with, call update_email. For questions about their "
        "earlier requests (refunds, changes), call get_my_requests."
    ),
}


FINAL_ANSWER_RULE = (
    "You have reached the tool limit for this message. Do not call any more tools. Answer now using only "
    "the information above. If it isn't enough, say what is missing and offer to connect a human agent."
)
FALLBACK_REPLY = ("Sorry, I couldn't finish looking into that. Could you rephrase it, "
                  "or would you like me to pass it to a human agent?")


def _run_agent(agent_name: str, state: SupportState) -> dict:
    messages = state.get("messages", [])
    # After MAX_TOOL_ROUNDS rounds of tool calls, the agent must answer with what it already has.
    # This stops search loops long before the graph's hard step limit is reached.
    out_of_tools = current_turn_tool_rounds(messages) >= get_settings().max_tool_rounds
    tools = AGENT_TOOLS[agent_name]
    llm = get_llm().bind_tools(tools, tool_choice="none") if out_of_tools else get_llm().bind_tools(tools)

    system_text = (f"{PROMPTS[agent_name]}\n\n{COMMON_RULES}\n"
                   f"Current customer ID: {state.get('customer_id', 'unknown')}.")
    if out_of_tools:
        system_text += f"\n\n{FINAL_ANSWER_RULE}"
    response = llm.invoke([SystemMessage(content=system_text)] + history_for_llm(messages))

    if out_of_tools and getattr(response, "tool_calls", None):
        # The model ignored tool_choice="none": drop the calls so the turn ends.
        response = AIMessage(content=response.content or FALLBACK_REPLY)
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
