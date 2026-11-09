"""Helpers for slicing the conversation into what each node actually needs."""
import json
from typing import List, Optional

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

from app.textnorm import normalize_text


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
            return normalize_text(str(msg.content))
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


def current_turn_retrieved(messages: List[AnyMessage], tool_name: str = "search_knowledge_base") -> List[str]:
    """Every document section the knowledge tool returned while answering the latest message."""
    idx = last_human_index(messages)
    sources: List[str] = []
    for msg in messages[(idx or 0):]:
        if isinstance(msg, ToolMessage) and msg.name == tool_name:
            try:
                results = json.loads(msg.content).get("results", [])
            except (ValueError, AttributeError):
                continue
            sources.extend(r["source"] for r in results if r.get("source") and r["source"] not in sources)
    return sources


def current_turn_sources(messages: List[AnyMessage], tool_name: str = "search_knowledge_base") -> List[str]:
    """Retrieved sections ("Title > Section") that the current reply actually cites in [brackets]."""
    idx = last_human_index(messages)
    sources: List[str] = []
    for msg in messages[(idx or 0):]:
        if isinstance(msg, ToolMessage) and msg.name == tool_name:
            try:
                results = json.loads(msg.content).get("results", [])
            except (ValueError, AttributeError):
                continue
            for r in results:
                if r.get("source") and r["source"] not in sources:
                    sources.append(r["source"])
    reply = current_turn_reply(messages)
    return [s for s in sources if f"[{s}]" in reply]


def current_turn_tool_calls(messages: List[AnyMessage]) -> List[dict]:
    """Tool calls the agents made while answering the latest message, in order."""
    idx = last_human_index(messages)
    calls: List[dict] = []
    for msg in messages[(idx or 0):]:
        if isinstance(msg, AIMessage):
            calls.extend({"name": c["name"], "args": c.get("args") or {}} for c in msg.tool_calls)
    return calls


def conversation_log(messages: List[AnyMessage]) -> List[dict]:
    """The chat as the customer saw it: their messages and the assistant's text replies, no tool traffic."""
    log: List[dict] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            log.append({"role": "customer", "content": str(msg.content)})
        elif isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            log.append({"role": "assistant", "content": normalize_text(str(msg.content))})
    return log


def current_turn_tool_rounds(messages: List[AnyMessage]) -> int:
    """How many times the agent has asked for tools while answering the latest message."""
    idx = last_human_index(messages)
    return sum(1 for m in messages[(idx or 0):] if isinstance(m, AIMessage) and m.tool_calls)
