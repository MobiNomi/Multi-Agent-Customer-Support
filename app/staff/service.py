"""What the staff console reads and does: the approval queue, the ticket inbox, replies and closing."""
import json
from typing import List, Optional, Tuple

from app.actions.service import ActionConflict, ActionNotFound, InvalidDecision, action_dict
from app.actions.updates import add_update, iso, update_dicts
from app.auth.service import AuthUser
from app.database.connection import session_scope
from app.database.models import (Customer, Order, Payment, PendingAction, SupportTicket, TicketReply,
                                 utcnow_naive)

PRIORITY_ORDER = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}
OPEN_TICKET_STATUSES = ("Open", "Escalated")


def _customer(c: Optional[Customer]) -> Optional[dict]:
    if c is None:
        return None
    return {"id": c.id, "name": c.name, "email": c.email, "plan": c.plan, "since": iso(c.created_at),
            "orders": [{"order_id": o.id, "amount": o.amount, "status": o.status} for o in c.orders]}


def _ticket(t: SupportTicket, name: Optional[str] = None) -> dict:
    return {"id": t.id, "customer_id": t.customer_id, "customer_name": name, "category": t.category,
            "priority": t.priority, "status": t.status, "description": t.description,
            "created_at": iso(t.created_at), "closed_at": iso(t.closed_at), "thread_id": t.thread_id,
            "has_conversation": bool(t.thread_id)}


# ---------- queue ----------

def queue() -> dict:
    """Refunds waiting for approval (oldest first), open tickets (most urgent first), recent decisions."""
    with session_scope() as s:
        names = dict(s.query(Customer.id, Customer.name).all())
        waiting = (s.query(PendingAction)
                   .filter(PendingAction.status == "awaiting_staff", PendingAction.requires_staff.is_(True))
                   .order_by(PendingAction.confirmed_at, PendingAction.created_at).all())
        tickets = s.query(SupportTicket).filter(SupportTicket.status.in_(OPEN_TICKET_STATUSES)).all()
        tickets.sort(key=lambda t: (PRIORITY_ORDER.get(t.priority, 9), -(t.created_at.timestamp()
                                                                        if t.created_at else 0)))
        recent = (s.query(PendingAction)
                  .filter(PendingAction.requires_staff.is_(True),
                          PendingAction.status.in_(("completed", "rejected", "failed")),
                          PendingAction.resolved_by.isnot(None))
                  .order_by(PendingAction.resolved_at.desc()).limit(15).all())

        def with_name(a):
            d = action_dict(a)
            d["customer_name"] = names.get(a.customer_id)
            d["resolved_by_name"] = names.get(a.resolved_by)
            return d

        return {
            "actions": [with_name(a) for a in waiting],
            "tickets": [_ticket(t, names.get(t.customer_id)) for t in tickets],
            "recent": [with_name(a) for a in recent],
        }


# ---------- one request ----------

def action_detail(action_id: str) -> dict:
    """Everything a staff member needs to decide: the request, the customer, the payment and its order."""
    with session_scope() as s:
        a = s.get(PendingAction, action_id)
        if a is None or not a.requires_staff:
            raise ActionNotFound("Request not found.")
        detail = action_dict(a)
        customer = s.get(Customer, a.customer_id)
        detail["customer"] = _customer(customer)
        names = dict(s.query(Customer.id, Customer.name).all())
        detail["resolved_by_name"] = names.get(a.resolved_by)
        data = json.loads(a.payload or "{}")
        if a.kind == "refund":
            p = s.query(Payment).filter_by(transaction_id=data.get("transaction_id")).first()
            order = s.get(Order, data.get("order_id")) if data.get("order_id") else None
            detail["payment"] = None if p is None else {
                "transaction_id": p.transaction_id, "amount": p.amount, "status": p.status,
                "created_at": iso(p.created_at)}
            detail["order"] = None if order is None else {
                "order_id": order.id, "amount": order.amount, "status": order.status,
                "payments": [{"transaction_id": q.transaction_id, "amount": q.amount, "status": q.status}
                             for q in order.payments]}
        if a.parent_id:
            parent = s.get(PendingAction, a.parent_id)
            detail["parent_summary"] = parent.summary if parent else None
        return detail


# ---------- tickets ----------

def _ticket_full(t: SupportTicket) -> dict:
    detail = _ticket(t, t.customer.name if t.customer else None)
    detail["customer"] = _customer(t.customer)
    detail["replies"] = [{"id": r.id, "author": r.author, "content": r.body, "created_at": iso(r.created_at)}
                         for r in t.replies]
    return detail


def ticket_detail(ticket_id: str) -> dict:
    with session_scope() as s:
        t = s.get(SupportTicket, ticket_id)
        if t is None:
            raise ActionNotFound("Ticket not found.")
        return _ticket_full(t)


def reply(ticket_id: str, staff: AuthUser, message: str) -> Tuple[dict, List[dict]]:
    """Save a staff reply on the ticket and send it to the customer's chat (if the ticket came from one)."""
    message = (message or "").strip()
    if len(message) < 2:
        raise InvalidDecision("Write a reply first.")
    if len(message) > 4000:
        raise InvalidDecision("Keep the reply under 4,000 characters.")
    with session_scope() as s:
        t = s.get(SupportTicket, ticket_id)
        if t is None:
            raise ActionNotFound("Ticket not found.")
        if t.status == "Closed":
            raise ActionConflict("This ticket is closed.")
        s.add(TicketReply(ticket_id=t.id, staff_id=staff.id, author=staff.name, body=message,
                          created_at=utcnow_naive()))
        update = add_update(s, t.thread_id, t.customer_id, "ticket_reply", message, author=staff.name,
                            ticket_id=t.id)
        s.flush()
        return _ticket_full(t), update_dicts([update])


def close(ticket_id: str, staff: AuthUser, message: str = "") -> Tuple[dict, List[dict]]:
    message = (message or "").strip()[:4000]
    with session_scope() as s:
        t = s.get(SupportTicket, ticket_id)
        if t is None:
            raise ActionNotFound("Ticket not found.")
        if t.status == "Closed":
            raise ActionConflict("This ticket is already closed.")
        t.status, t.closed_at = "Closed", utcnow_naive()
        body = message or (f"We've closed ticket {t.id}. If you need anything else, just send a message here.")
        if message:
            s.add(TicketReply(ticket_id=t.id, staff_id=staff.id, author=staff.name, body=message,
                              created_at=utcnow_naive()))
        update = add_update(s, t.thread_id, t.customer_id, "ticket_closed", body, author=staff.name,
                            ticket_id=t.id)
        s.flush()
        return _ticket_full(t), update_dicts([update])
