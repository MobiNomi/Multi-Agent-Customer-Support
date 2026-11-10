"""Database tools the agents can call.

`customer_id` is an injected argument: the model never sees it in the tool schema, and the
tool executor fills it from the authenticated session. A customer can therefore only ever
read or write their own records, whatever the model is talked into asking for.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional

from langchain_core.tools import InjectedToolArg, tool

from app.database.connection import session_scope
from app.database.models import Customer, Order, Payment, SupportTicket

TicketCategory = Literal["Billing", "Technical", "General"]
TicketPriority = Literal["Low", "Medium", "High", "Urgent"]


def customer_exists(customer_id: str) -> bool:
    with session_scope() as session:
        return session.get(Customer, customer_id) is not None


def open_ticket(customer_id: str, category: str, description: str,
                priority: str = "Medium", status: str = "Open", thread_id: Optional[str] = None) -> str:
    """Insert a ticket and return its ID. Shared by the tool and the escalation node.

    `thread_id` links the ticket to the chat it came from, so staff replies appear in that chat.
    """
    ticket_id = f"TCK-{uuid.uuid4().hex[:8].upper()}"
    with session_scope() as session:
        session.add(SupportTicket(
            id=ticket_id,
            customer_id=customer_id,
            category=category,
            priority=priority,
            status=status,
            description=description[:2000],
            thread_id=thread_id,
        ))
    return ticket_id


@tool
def get_customer_details(customer_id: Annotated[str, InjectedToolArg]) -> Dict[str, Any]:
    """Look up the current customer's profile, plan, orders and support tickets."""
    with session_scope() as session:
        customer = session.get(Customer, customer_id)
        if not customer:
            return {"error": f"No customer found with ID {customer_id}"}
        return {
            "customer_id": customer.id,
            "name": customer.name,
            "email": customer.email,
            "plan": customer.plan,
            "orders": [{"order_id": o.id, "amount": o.amount, "status": o.status} for o in customer.orders],
            "tickets": [{"ticket_id": t.id, "category": t.category, "status": t.status} for t in customer.tickets],
        }


@tool
def get_order_payments(order_id: str, customer_id: Annotated[str, InjectedToolArg]) -> Dict[str, Any]:
    """Get every payment recorded against one of the current customer's orders (e.g. ORD-1034)."""
    with session_scope() as session:
        order = session.get(Order, order_id.strip().upper())
        # Same answer for "doesn't exist" and "belongs to someone else", so nothing leaks.
        if not order or order.customer_id != customer_id:
            return {"error": f"Order {order_id} was not found on this account."}
        payments = session.query(Payment).filter_by(order_id=order.id).order_by(Payment.created_at).all()
        return {
            "order_id": order.id,
            "order_status": order.status,
            "total_order_amount": order.amount,
            "payment_records_count": len(payments),
            "payments": [
                {
                    "payment_id": p.id,
                    "amount": p.amount,
                    "status": p.status,
                    "transaction_id": p.transaction_id,
                    "created_at": str(p.created_at),
                }
                for p in payments
            ],
        }


@tool
def create_support_ticket(
    category: TicketCategory,
    description: str,
    customer_id: Annotated[str, InjectedToolArg],
    priority: TicketPriority = "Medium",
    thread_id: Annotated[Optional[str], InjectedToolArg] = None,
) -> Dict[str, Any]:
    """Open a support ticket for follow-up by the support team (bugs, outages, anything you cannot resolve).
    For refunds use request_refund instead."""
    try:
        ticket_id = open_ticket(customer_id, category, description, priority=priority, thread_id=thread_id)
    except Exception as exc:  # surfaced to the model as data, not a crash
        return {"error": f"Failed to create ticket: {exc}"}
    return {"status": "success", "ticket_id": ticket_id, "message": f"Support ticket {ticket_id} created."}


def recent_tickets(customer_id: str, within_minutes: int = 60) -> List[Dict[str, Any]]:
    """The customer's tickets opened in the last `within_minutes`, newest first."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=within_minutes)
    with session_scope() as session:
        rows = (session.query(SupportTicket)
                .filter(SupportTicket.customer_id == customer_id, SupportTicket.created_at >= since)
                .order_by(SupportTicket.created_at.desc()).all())
        return [{"ticket_id": t.id, "category": t.category, "status": t.status} for t in rows]

