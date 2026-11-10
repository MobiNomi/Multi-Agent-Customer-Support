"""The guardrail steps inside the LangGraph flow."""
from functools import lru_cache
from typing import List, Optional

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

from app.config import get_settings
from app.graph.history import current_turn_proposed_action, current_turn_retrieved, latest_user_text
from app.graph.state import SupportState
from app.guardrails.injection import detect_injection
from app.guardrails.notes import note, record
from app.guardrails.output import REFUND_DONE, OutputGuard
from app.textnorm import normalize_text

INJECTION_REPLY = ("I can only help with questions about AcmeCloud and your account, and I can't change how I "
                   "work. What can I help you with?")
OFF_TOPIC_REPLY = ("I can only help with AcmeCloud: your account, billing, orders, the API and our plans. "
                   "Is there anything about those I can help with?")
UNSAFE_REPLY = ("I can't help with that. If you or someone else is in danger, please contact local emergency "
                "services. For anything about your AcmeCloud account, I'm here to help.")


def _thread(config: Optional[RunnableConfig]) -> Optional[str]:
    return ((config or {}).get("configurable") or {}).get("thread_id")


def _with_notes(state: SupportState, kinds: List[str]) -> List[dict]:
    return list(state.get("guardrail_notes") or []) + [note(k) for k in kinds]


@lru_cache(maxsize=1)
def output_guard() -> OutputGuard:
    # Everything the model is told privately; replies must never repeat it.
    from app.graph.agents import COMMON_RULES, FINAL_ANSWER_RULE, PROMPTS
    from app.graph.router import ROUTER_PROMPT
    return OutputGuard(list(PROMPTS.values()) + [COMMON_RULES, FINAL_ANSWER_RULE, ROUTER_PROMPT])


def _has_completed_refund(customer_id: Optional[str]) -> bool:
    if not customer_id:
        return False
    from app.database.connection import session_scope
    from app.database.models import PendingAction
    with session_scope() as session:
        return session.query(PendingAction).filter_by(customer_id=customer_id, kind="refund",
                                                      status="completed").first() is not None


def input_guard_node(state: SupportState, config: RunnableConfig = None) -> dict:
    """Stop instruction-override attempts before they reach any model."""
    if not get_settings().guardrails_enabled:
        return {}
    pattern = detect_injection(latest_user_text(state.get("messages", [])))
    if not pattern:
        return {}
    record(["injection_blocked"], state.get("customer_id"), _thread(config), detail=f"pattern={pattern}")
    return {
        "messages": [AIMessage(content=INJECTION_REPLY)],
        "intent": "blocked",
        "assigned_agent": "guardrail",
        "confidence": 1.0,
        "needs_human": False,
        "guardrail_notes": _with_notes(state, ["injection_blocked"]),
    }


def route_after_input_guard(state: SupportState) -> str:
    # The guard answered (last message is the assistant's) -> finish; otherwise go to the router.
    return "end" if isinstance(state["messages"][-1], AIMessage) else "supervisor"


def refusal_node(state: SupportState, config: RunnableConfig = None) -> dict:
    """Fixed reply for off-topic or unsafe requests. No agent, no tools, no cost."""
    kind = "unsafe" if state.get("intent") == "unsafe" else "off_topic"
    record([kind], state.get("customer_id"), _thread(config))
    return {
        "messages": [AIMessage(content=UNSAFE_REPLY if kind == "unsafe" else OFF_TOPIC_REPLY)],
        "assigned_agent": "guardrail",
        "guardrail_notes": _with_notes(state, [kind]),
    }


def output_guard_node(state: SupportState, config: RunnableConfig = None) -> dict:
    """Check the agent's final reply and fix it in place before the customer sees it."""
    if not get_settings().guardrails_enabled:
        return {}
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage) or not last.content:
        return {}
    text = normalize_text(str(last.content))
    proposed = current_turn_proposed_action(messages)
    # "Your refund was approved" is fine once staff really have approved one; only then ask the database.
    refund_completed = (not proposed and any(p.search(text) for p in REFUND_DONE)
                        and _has_completed_refund(state.get("customer_id")))
    reply, kinds = output_guard().check(text, current_turn_retrieved(messages), proposed_action=proposed,
                                        refund_completed=refund_completed)
    if not kinds:
        return {}
    record(kinds, state.get("customer_id"), _thread(config))
    # Same message id: the add_messages reducer replaces the reply instead of adding a second one.
    return {"messages": [AIMessage(content=reply, id=last.id)], "guardrail_notes": _with_notes(state, kinds)}
