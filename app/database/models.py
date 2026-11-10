from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_naive() -> datetime:
    """Current UTC time without a timezone, which is how SQLite gives datetimes back."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True)  # e.g. CUS-1001
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    plan = Column(String, default="Pro")    # Free, Pro, Enterprise
    created_at = Column(DateTime, default=utcnow)

    # Login. Passwords are stored only as Argon2 hashes, never in plain text.
    password_hash = Column(String, nullable=True)            # None = this customer can't sign in yet
    role = Column(String, nullable=False, default="customer", server_default="customer")
    failed_logins = Column(Integer, nullable=False, default=0, server_default="0")
    locked_until = Column(DateTime, nullable=True)           # naive UTC
    last_login_at = Column(DateTime, nullable=True)          # naive UTC
    token_version = Column(Integer, nullable=False, default=0, server_default="0")  # bump to sign out everywhere

    orders = relationship("Order", back_populates="customer")
    tickets = relationship("SupportTicket", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id = Column(String, primary_key=True)  # e.g. ORD-1034
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False) # Processing, Delivered, Cancelled
    created_at = Column(DateTime, default=utcnow)

    customer = relationship("Customer", back_populates="orders")
    payments = relationship("Payment", back_populates="order")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String, primary_key=True)  # e.g. PAY-5001
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    order_id = Column(String, ForeignKey("orders.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False) # Successful, Refunded, Duplicate
    transaction_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)

    order = relationship("Order", back_populates="payments")


class SupportTicket(Base):
    __tablename__ = "tickets"

    id = Column(String, primary_key=True)  # e.g. TCK-9001
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    category = Column(String, nullable=False)  # Billing, Technical, General
    priority = Column(String, default="Medium")  # Low, Medium, High, Urgent
    status = Column(String, default="Open")    # Open, Escalated, Closed
    description = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    thread_id = Column(String, nullable=True, index=True)   # the chat it came from, so staff replies reach it
    closed_at = Column(DateTime, nullable=True)

    customer = relationship("Customer", back_populates="tickets")
    replies = relationship("TicketReply", back_populates="ticket", order_by="TicketReply.id")


class TicketReply(Base):
    """A message a staff member wrote on a ticket."""
    __tablename__ = "ticket_replies"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(String, ForeignKey("tickets.id"), nullable=False, index=True)
    staff_id = Column(String, nullable=False)
    author = Column(String, nullable=False)
    body = Column(String, nullable=False)
    created_at = Column(DateTime, default=utcnow_naive)

    ticket = relationship("SupportTicket", back_populates="replies")


class PendingAction(Base):
    """Something the assistant proposed that changes the account (refund, email change, cancellation).

    Nothing happens until the customer confirms it. Refunds also need a staff member's approval.
    Status: awaiting_customer -> (awaiting_staff ->) processing -> completed | failed,
            or cancelled (by the customer), expired (not confirmed in time), rejected (by staff).
    """
    __tablename__ = "pending_actions"
    id = Column(String, primary_key=True)                     # e.g. ACT-1A2B3C4D
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False, index=True)
    thread_id = Column(String, nullable=True, index=True)     # the chat where it was proposed
    kind = Column(String, nullable=False)                     # refund, update_email, cancel_order
    subject = Column(String, nullable=False)                  # what it acts on: TXN-8922, ORD-1035, "email"
    status = Column(String, nullable=False, index=True)
    summary = Column(String, nullable=False)
    reason = Column(String, nullable=False, default="")
    payload = Column(String, nullable=False, default="{}")    # JSON with what the executor needs
    amount = Column(Float, nullable=True)
    requires_staff = Column(Boolean, nullable=False, default=False)
    parent_id = Column(String, nullable=True)                 # e.g. the cancellation that created this refund
    created_at = Column(DateTime, default=utcnow_naive, index=True)
    expires_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)               # staff ID, or "customer" / "system"
    resolution_note = Column(String, nullable=True)           # staff note or rejection reason
    result = Column(String, nullable=True)                    # what the executor did, or why it failed


class ConversationUpdate(Base):
    """A message for the customer's chat that didn't come from the assistant.

    Staff decisions and replies, and the outcome of confirmed actions. The chat page polls for these,
    and they are copied into the conversation memory before the customer's next message.
    """
    __tablename__ = "conversation_updates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, nullable=False, index=True)
    customer_id = Column(String, ForeignKey("customers.id"), nullable=False)
    kind = Column(String, nullable=False)          # action_submitted, action_completed, staff_approved, ...
    author = Column(String, nullable=True)         # staff member's name; empty for automatic updates
    body = Column(String, nullable=False)
    action_id = Column(String, nullable=True)
    ticket_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)


class GuardrailEvent(Base):
    """One row each time a guardrail fires. Never contains the sensitive text itself."""
    __tablename__ = "guardrail_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=utcnow, index=True)
    customer_id = Column(String, index=True, nullable=True)
    thread_id = Column(String, nullable=True)
    stage = Column(String, nullable=False)      # input, router, action, output
    kind = Column(String, nullable=False, index=True)
    detail = Column(String, default="")

