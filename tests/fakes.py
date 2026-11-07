"""A stand-in for ChatGroq so the graph can be tested offline and deterministically."""
from typing import Iterable, List, Optional

from langchain_core.messages import AIMessage


def tool_call(name: str, args: Optional[dict] = None, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


class FakeLLM:
    """Scripted agent replies plus a keyword-based router.

    - `agent_replies` are returned in order by plain `.invoke()` (the agents).
    - `.with_structured_output()` returns a router that classifies by keywords.
    - Every agent call is recorded in `.calls`, every bind in `.bound`.
    """

    def __init__(self, agent_replies: Iterable[AIMessage] = ()):
        self.agent_replies: List[AIMessage] = list(agent_replies)
        self.calls: List[list] = []
        self.bound: List[List[str]] = []
        self.router_inputs: List[str] = []

    def bind_tools(self, tools):
        self.bound.append([t.name for t in tools])
        return self

    def with_structured_output(self, schema):
        return _KeywordRouter(self, schema)

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self.agent_replies:
            return AIMessage(content="(no scripted reply)")
        return self.agent_replies.pop(0)


class _KeywordRouter:
    def __init__(self, parent: FakeLLM, schema):
        self.parent, self.schema = parent, schema

    def invoke(self, messages):
        text = str(messages[-1].content)
        self.parent.router_inputs.append(text)
        latest = text.split("Latest message:")[-1].lower()
        if any(w in latest for w in ("human", "lawyer", "manager")):
            return self.schema(intent="escalate", confidence=0.95, needs_human=True)
        if any(w in latest for w in ("charge", "refund", "invoice", "payment", "billing")):
            return self.schema(intent="billing", confidence=0.9, needs_human=False)
        if any(w in latest for w in ("api", "error", "bug", "login", "crash")):
            return self.schema(intent="technical", confidence=0.9, needs_human=False)
        return self.schema(intent="general", confidence=0.6, needs_human=False)
