import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.graph.history import latest_user_text, transcript
from app.graph.state import SupportState
from app.llm import get_llm

logger = logging.getLogger(__name__)

INTENT_TO_AGENT = {
    "billing": "billing_agent",
    "technical": "technical_agent",
    "general": "general_agent",
    "escalate": "human_escalation",
    "off_topic": "refusal",
    "unsafe": "refusal",
}

ROUTER_PROMPT = (
    "You are the intent classifier for a customer support system.\n"
    "Classify the customer's LATEST message into exactly one intent:\n"
    "- billing: subscriptions, charges, duplicate charges, refunds, invoices, failed payments, orders and "
    "cancelling an order, and the status of a refund request.\n"
    "- technical: bugs, API errors, configuration, login or performance problems.\n"
    "- general: opening hours, policies, plan features, changing account details such as the sign-in "
    "email, greetings, anything else.\n"
    "- escalate: asks for a human, threatens legal action, or is extremely angry.\n"
    "- off_topic: has nothing to do with AcmeCloud or the customer's account, e.g. homework, essays, coding "
    "tasks unrelated to the AcmeCloud API, general trivia, other companies' products.\n"
    "- unsafe: harassment, hate, threats, sexual content, or requests for illegal or harmful help.\n"
    "When unsure between general and off_topic, choose general.\n"
    "Use the earlier conversation only to understand short follow-ups such as 'yes, do it'.\n"
    "Set needs_human to true only for the escalate cases."
)


class RoutingDecision(BaseModel):
    """Structured output of the supervisor."""
    intent: Literal["billing", "technical", "general", "escalate", "off_topic", "unsafe"] = Field(
        description="Primary intent of the customer's latest message."
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence between 0.0 and 1.0.")
    needs_human: bool = Field(
        default=False,
        description="True if the customer asks for a human, threatens legal action or is extremely frustrated.",
    )


def supervisor_router_node(state: SupportState) -> dict:
    """Classify the latest customer message and pick the next node."""
    messages = state.get("messages", [])
    latest = latest_user_text(messages)
    if not latest:
        return {"intent": "general", "assigned_agent": "general_agent", "confidence": 0.0, "needs_human": False}

    context = transcript(messages)
    user_block = (f"Earlier conversation:\n{context}\n\n" if context else "") + f"Latest message: {latest}"

    # Outside the try: a missing API key must surface as a configuration error, not a silent fallback.
    router = get_llm().with_structured_output(RoutingDecision)
    try:
        decision: RoutingDecision = router.invoke([SystemMessage(content=ROUTER_PROMPT),
                                                   HumanMessage(content=user_block)])
    except Exception:
        logger.exception("Router failed; falling back to general_agent")
        return {"intent": "general", "assigned_agent": "general_agent", "confidence": 0.0, "needs_human": False}

    needs_human = decision.needs_human or decision.intent == "escalate"
    intent = "escalate" if needs_human else decision.intent
    # The agent is derived from the intent in code, so the two can never disagree.
    return {
        "intent": intent,
        "assigned_agent": INTENT_TO_AGENT[intent],
        "confidence": decision.confidence,
        "needs_human": needs_human,
    }
