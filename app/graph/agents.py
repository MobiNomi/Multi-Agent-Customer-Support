import os
from dotenv import load_dotenv

# Load environment variables FIRST before initializing LangChain models
load_dotenv()

from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage
from app.graph.state import SupportState
from app.tools.db_tools import get_customer_details, get_order_payments, create_support_ticket

# Initialize Groq LLM using .env variables
model_name = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
llm = ChatGroq(
    groq_api_key=os.getenv("GROQ_API_KEY"),
    model_name=model_name,
    temperature=0.0
)

billing_llm = llm.bind_tools([get_customer_details, get_order_payments, create_support_ticket])


def billing_agent_node(state: SupportState) -> SupportState:
    """Specialized agent node for resolving billing and payment issues."""
    system_prompt = SystemMessage(content=(
        "You are a specialized Billing Support Agent. Your task is to investigate and resolve payment, "
        "refund, double-charge, or invoice inquiries.\n"
        "Use the provided database tools (`get_customer_details`, `get_order_payments`, `create_support_ticket`) "
        "to check the exact transaction history before answering.\n"
        "If a duplicate payment is confirmed, acknowledge the duplicate transaction ID and inform the customer "
        "that a refund/ticket has been initiated."
    ))

    messages = [system_prompt] + state["messages"]
    response = billing_llm.invoke(messages)

    state["messages"].append(response)
    state["assigned_agent"] = "billing_agent"
    return state


def technical_agent_node(state: SupportState) -> SupportState:
    """Specialized agent node for resolving technical/API/platform issues."""
    system_prompt = SystemMessage(content=(
        "You are a Technical Support Specialist. Provide concise troubleshooting steps for "
        "platform issues, API integrations, or account errors. Use tools if customer context is needed."
    ))

    tech_llm = llm.bind_tools([get_customer_details, create_support_ticket])
    messages = [system_prompt] + state["messages"]
    response = tech_llm.invoke(messages)

    state["messages"].append(response)
    state["assigned_agent"] = "technical_agent"
    return state


def general_agent_node(state: SupportState) -> SupportState:
    """Agent node for general inquiries and onboarding."""
    system_prompt = SystemMessage(content=(
        "You are a General Customer Support Agent. Answer general questions about services, "
        "operating hours, and plan features clearly and politely."
    ))

    messages = [system_prompt] + state["messages"]
    response = llm.invoke(messages)

    state["messages"].append(response)
    state["assigned_agent"] = "general_agent"
    return state