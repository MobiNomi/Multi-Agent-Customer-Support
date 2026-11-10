"""Request and response models for the actions and staff console endpoints."""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class ActionOut(BaseModel):
    """A change the assistant proposed, and where it stands."""
    id: str
    kind: Literal["refund", "update_email", "cancel_order"]
    kind_label: str
    status: Literal["awaiting_customer", "awaiting_staff", "processing", "completed", "rejected",
                    "cancelled", "expired", "failed"]
    status_text: str
    summary: str
    amount: Optional[float] = None
    requires_staff: bool
    created_at: Optional[str] = None
    expires_at: Optional[str] = Field(default=None, description="Unconfirmed requests lapse at this time.")
    confirmed_at: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_note: Optional[str] = Field(default=None, description="Staff note, or the reason for a rejection.")
    result: Optional[str] = None


class UpdateOut(BaseModel):
    """A message for the chat that didn't come from the assistant."""
    id: int
    kind: str = Field(description="action_submitted, action_completed, action_cancelled, action_failed, "
                                  "staff_approved, staff_rejected, ticket_reply or ticket_closed")
    author: Optional[str] = Field(default=None, description="Staff member's name; empty for automatic updates.")
    content: str
    created_at: Optional[str] = None
    action_id: Optional[str] = None
    ticket_id: Optional[str] = None


class ActionResult(BaseModel):
    action: ActionOut
    updates: List[UpdateOut] = Field(default_factory=list, description="Messages added to the chat by this step.")


class SessionUpdates(BaseModel):
    updates: List[UpdateOut]
    actions: List[ActionOut] = Field(description="Every request proposed in this conversation, with its status.")
    last_update_id: int


# ---------- staff console ----------

class StaffAccount(BaseModel):
    id: str
    name: str
    email: str


class StaffLoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254, examples=["staff@acmecloud.example"])
    password: str = Field(..., min_length=1, max_length=256)


class StaffLoginResponse(BaseModel):
    staff: StaffAccount
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class ConversationMessage(BaseModel):
    role: Literal["customer", "assistant", "support"]
    content: str
    author: Optional[str] = None
    kind: Optional[str] = None
    created_at: Optional[str] = None


class CustomerSummary(BaseModel):
    id: str
    name: str
    email: str
    plan: str
    since: Optional[str] = None
    orders: List[dict] = Field(default_factory=list)


class QueueAction(ActionOut):
    customer_id: str
    customer_name: Optional[str] = None
    reason: str = ""
    resolved_by: Optional[str] = None
    resolved_by_name: Optional[str] = None


class QueueTicket(BaseModel):
    id: str
    customer_id: str
    customer_name: Optional[str] = None
    category: str
    priority: Optional[str] = None
    status: str
    description: str
    created_at: Optional[str] = None
    closed_at: Optional[str] = None
    has_conversation: bool


class StaffQueue(BaseModel):
    actions: List[QueueAction] = Field(description="Refunds waiting for approval, oldest first.")
    tickets: List[QueueTicket] = Field(description="Open and escalated tickets, most urgent first.")
    recent: List[QueueAction] = Field(description="The latest staff decisions.")


class ActionDetail(QueueAction):
    customer: Optional[CustomerSummary] = None
    payment: Optional[dict] = None
    order: Optional[dict] = None
    parent_summary: Optional[str] = None
    conversation: List[ConversationMessage] = Field(default_factory=list)


class TicketReplyOut(BaseModel):
    id: int
    author: str
    content: str
    created_at: Optional[str] = None


class TicketDetail(QueueTicket):
    customer: Optional[CustomerSummary] = None
    replies: List[TicketReplyOut] = Field(default_factory=list)
    conversation: List[ConversationMessage] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    note: str = Field(default="", max_length=1000, description="Optional note added to the customer's message.")


class RejectRequest(BaseModel):
    reason: str = Field(..., max_length=1000, description="Shown to the customer.")


class ReplyRequest(BaseModel):
    message: str = Field(..., max_length=4000)


class CloseRequest(BaseModel):
    message: str = Field(default="", max_length=4000, description="Optional closing message for the customer.")


class StaffDecision(BaseModel):
    action: ActionOut
    updates: List[UpdateOut] = Field(default_factory=list)
    delivered_to_chat: bool = Field(description="False if the request has no chat to send the message to.")


class TicketResult(BaseModel):
    ticket: TicketDetail
    delivered_to_chat: bool = Field(description="False if the ticket didn't come from a chat, so the customer "
                                                "can't see the message there.")
