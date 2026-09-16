import os
from typing import Literal
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage
from app.graph.state import SupportState


class RoutingDecision(BaseModel):
    """Pydantic schema for structured output routing."""
    intent: Literal["billing", "technical", "general", "escalate"] = Field(
        description="The primary intent of the customer message."
    )
    assigned_agent: Literal["billing_agent", "technical_agent", "general_agent", "human_escalation"] = Field(
        description="The appropriate downstream agent or node."
    )
    confidence: float = Field(
        description="Confidence score between 0.0 and 1.0."
    )
    needs_human: bool = Field(
        default=False,
        description="Set to true if user demands human agent, expresses extreme frustration, or has complex unresolved issues."
    )


def create_router_llm():
    model_name = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
    llm = ChatGroq(
        groq_api_key=os.getenv("GROQ_API_KEY"),
        model_name=model_name,
        temperature=0.0
    )
    return llm.with_structured_output(RoutingDecision)


def supervisor_router_node(state: SupportState) -> dict:
    """Supervisor router node to evaluate incoming messages and direct state flow."""
    messages = state.get("messages", [])
    if not messages:
        return {
            "intent": "general",
            "assigned_agent": "general_agent",
            "confidence": 0.5,
            "needs_human": False
        }

    latest_user_msg = messages[-1].content

    # System prompt framing the classification rules
    system_prompt = (
        "You are an expert intent classifier for a customer support AI graph.\n"
        "Analyze the user message and classify into one of four routes:\n\n"
        "1. 'billing': Subscription plans, duplicate charges, refunds, invoice requests, payment failures.\n"
        "2. 'technical': Software bugs, API errors, configuration issues, performance problems.\n"
        "3. 'general': Operating hours, policy questions, high-level features, greetings.\n"
        "4. 'escalate': Demands human agent, legal threats, extreme dissatisfaction, unresolvable cases.\n\n"
        "Select the appropriate assigned_agent ('billing_agent', 'technical_agent', 'general_agent', or 'human_escalation')."
    )

    structured_llm = create_router_llm()
    
    try:
        decision: RoutingDecision = structured_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"User Query: {latest_user_msg}")
        ])

        return {
            "intent": decision.intent,
            "assigned_agent": decision.assigned_agent,
            "confidence": decision.confidence,
            "needs_human": decision.needs_human
        }
    except Exception as err:
        # Fallback safety default on parsing error
        return {
            "intent": "general",
            "assigned_agent": "general_agent",
            "confidence": 0.0,
            "needs_human": False
        }