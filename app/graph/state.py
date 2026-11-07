from typing import Annotated, List, Optional, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SupportState(TypedDict, total=False):
    # Full conversation for this session; add_messages appends instead of overwriting.
    messages: Annotated[List[AnyMessage], add_messages]
    customer_id: str
    # Set by the supervisor each turn.
    intent: Optional[str]
    assigned_agent: Optional[str]
    confidence: float
    needs_human: bool
    # Set when a ticket is opened during the current turn.
    ticket_id: Optional[str]
    resolution: Optional[str]
