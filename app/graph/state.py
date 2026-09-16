from typing import Annotated, TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class SupportState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    customer_id: str
    intent: Optional[str]
    assigned_agent: Optional[str]
    confidence: float
    tool_results: Dict[str, Any]
    resolution: Optional[str]
    needs_human: bool
    ticket_id: Optional[str]