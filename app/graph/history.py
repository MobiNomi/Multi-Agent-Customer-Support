"""Helpers for slicing the conversation into what each node actually needs."""
from typing import List, Optional

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage


def last_human_index(messages: List[AnyMessage]) -> Optional[int]:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return i
    return None


def latest_user_text(messages: List[AnyMessage]) -> str:
    idx = last_human_index(messages)
    return str(messages[idx].content) if idx is not None else ""


def history_for_llm(messages: List[AnyMessage]) -> List[AnyMessage]:
    """Earlier turns as plain text, plus the full current turn.

    Tool calls and tool results from earlier turns are dropped: the agent's text reply
    already summarised them, they waste tokens, and a tool call made by one agent can be
    rejected by the API when a different agent (with different tools bound) sees it later.
    Tool traffic from the *current* turn is kept so the agent can read its own results.
    """
    idx = last_human_index(messages)
    if idx is None:
        return list(messages)
    earlier: List[AnyMessage] = []
    for msg in messages[:idx]:
        if isinstance(msg, HumanMessage):
            earlier.append(msg)
        elif isinstance(msg, AIMessage) and msg.content:
            earlier.append(AIMessage(content=msg.content))
    return earlier + list(messages[idx:])


def current_turn_reply(messages: List[AnyMessage]) -> str:
    """Text of the last AI message produced after the latest user message."""
    idx = last_human_index(messages)
    for msg in reversed(messages[(idx or 0):]):
        if isinstance(msg, AIMessage) and msg.content:
            return str(msg.content)
    return ""


def transcript(messages: List[AnyMessage], limit: int = 6) -> str:
    """Short plain-text transcript of the turns before the latest user message."""
    idx = last_human_index(messages)
    lines = []
    for msg in messages[: idx or 0]:
        if isinstance(msg, HumanMessage):
            lines.append(f"Customer: {msg.content}")
        elif isinstance(msg, AIMessage) and msg.content:
            lines.append(f"Support: {msg.content}")
    return "\n".join(lines[-limit:])
