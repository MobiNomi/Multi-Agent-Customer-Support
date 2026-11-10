"""Actions with confirmation: the assistant proposes, the customer confirms, staff approve refunds.

The assistant never changes an account directly. Its action tools only create a PendingAction row and
show the customer Confirm / Cancel buttons. What happens next is decided outside the model:

    refund        customer confirms -> billing staff approve or reject -> payment marked Refunded
    update_email  customer confirms -> email changed straight away
    cancel_order  customer confirms -> order cancelled straight away; any paid amount becomes a refund
                  request that goes to billing staff

Every state change is a conditional UPDATE ("... WHERE status = <expected>"), so a double click or two
staff members approving at the same moment can't run an action twice.
"""
import json
import logging
import uuid
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy import func

from app.actions.updates import add_update, iso, update_dicts
from app.auth.service import EMAIL_PATTERN, AuthUser
from app.config import get_settings
from app.database.connection import session_scope
from app.database.models import Customer, Order, Payment, PendingAction, utcnow_naive

logger = logging.getLogger(__name__)

KIND_LABELS = {"refund": "Refund", "update_email": "Change sign-in email", "cancel_order": "Cancel order"}
OPEN_STATUSES = ("awaiting_customer", "awaiting_staff")
STATUS_TEXT = {
    "awaiting_customer": "Waiting for you to confirm",
    "awaiting_staff": "With the billing team for approval",
    "processing": "Being processed",
    "completed": "Done",
    "rejected": "Declined by the support team",
    "cancelled": "Cancelled",
    "expired": "Expired before it was confirmed",
    "failed": "Couldn't be completed",
}
STAFF_DECIDES = ("staff",)   # roles allowed to approve and reject


# ---------- errors ----------

class ActionError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ActionNotFound(ActionError):
    status_code = 404


class ActionConflict(ActionError):
    status_code = 409


class ActionExpired(ActionError):
    status_code = 410


class InvalidDecision(ActionError):
    status_code = 422


class ExecutionFailed(Exception):
    """The action was allowed but can't be carried out any more (e.g. the payment was refunded meanwhile)."""


# ---------- helpers ----------

def money(amount: Optional[float]) -> str:
    return f"${amount:,.2f}" if amount is not None else ""


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _sentence(text: str) -> str:
    text = " ".join((text or "").split())
    return text if not text or text[-1] in ".!?" else text + "."


def _new_id() -> str:
    return f"ACT-{uuid.uuid4().hex[:8].upper()}"


def _window() -> timedelta:
    return timedelta(minutes=get_settings().action_confirm_minutes)


def action_dict(a: PendingAction) -> dict:
    return {
        "id": a.id, "kind": a.kind, "kind_label": KIND_LABELS.get(a.kind, a.kind), "subject": a.subject,
        "status": a.status, "status_text": STATUS_TEXT.get(a.status, a.status), "summary": a.summary,
        "reason": a.reason or "", "amount": a.amount, "requires_staff": bool(a.requires_staff),
        "customer_id": a.customer_id, "thread_id": a.thread_id, "parent_id": a.parent_id,
        "created_at": iso(a.created_at), "expires_at": iso(a.expires_at), "confirmed_at": iso(a.confirmed_at),
        "resolved_at": iso(a.resolved_at), "resolved_by": a.resolved_by,
        "resolution_note": a.resolution_note, "result": a.result,
    }


def _expire_if_due(a: PendingAction, now) -> bool:
    """Unconfirmed proposals lapse after ACTION_CONFIRM_MINUTES (checked whenever one is read)."""
    if a.status == "awaiting_customer" and a.expires_at and a.expires_at <= now:
        a.status, a.resolved_at, a.resolved_by = "expired", now, "system"
        return True
    return False


def _open_action(session, customer_id: str, kind: str, subject: str, now) -> Optional[PendingAction]:
    rows = (session.query(PendingAction)
            .filter(PendingAction.customer_id == customer_id, PendingAction.kind == kind,
                    PendingAction.subject == subject, PendingAction.status.in_(OPEN_STATUSES))
            .order_by(PendingAction.created_at.desc()).all())
    for a in rows:
        if not _expire_if_due(a, now):
            return a
    return None


def _claim(session, action_id: str, from_status: str, to_status: str, **fields) -> bool:
    """Move an action from one status to another only if nobody changed it first."""
    n = (session.query(PendingAction)
         .filter(PendingAction.id == action_id, PendingAction.status == from_status)
         .update({"status": to_status, **fields}, synchronize_session=False))
    return n == 1


# ---------- proposals (called by the assistant's tools) ----------

CONFIRM_NOTES = {
    "refund": ("Nothing has been refunded yet. The customer now sees Confirm and Cancel buttons under your "
               "reply. Ask them to press Confirm to send the request; the billing team then reviews it within "
               "1 business day. Don't say the refund is done or approved."),
    "update_email": ("Nothing has changed yet. The customer now sees Confirm and Cancel buttons under your "
                     "reply, and the email changes as soon as they press Confirm. Don't say it has already "
                     "changed."),
    "cancel_order": ("Nothing has changed yet. The customer now sees Confirm and Cancel buttons under your "
                     "reply. The order is cancelled as soon as they press Confirm{refund}. Don't say it is "
                     "already cancelled."),
}


def _proposal(a: PendingAction) -> dict:
    note = CONFIRM_NOTES[a.kind]
    if a.kind == "cancel_order":
        has_refund = bool(json.loads(a.payload).get("refunds"))
        note = note.format(refund=", and the refund then goes to the billing team for approval" if has_refund else "")
    return {"status": "needs_confirmation", "action_id": a.id, "summary": a.summary, "instructions": note}


def propose_refund(customer_id: str, thread_id: Optional[str], transaction_id: str, reason: str = "") -> dict:
    txn = (transaction_id or "").strip().upper()
    if not txn:
        return {"error": "Give the transaction ID of the payment to refund, for example TXN-8922."}
    now = utcnow_naive()
    with session_scope() as s:
        p = s.query(Payment).filter(Payment.transaction_id == txn).first()
        if p is None or p.customer_id != customer_id:   # same answer for "missing" and "someone else's"
            return {"error": f"Payment {txn} was not found on this account. Ask the customer to check the ID."}
        if p.status == "Refunded":
            return {"error": f"Payment {txn} has already been refunded, so there is nothing to refund."}
        existing = _open_action(s, customer_id, "refund", txn, now)
        if existing is not None and existing.status == "awaiting_staff":
            return {"status": "already_requested", "action_id": existing.id, "summary": existing.summary,
                    "message": (f"A refund for {txn} was already confirmed and is waiting for the billing team's "
                                "review. Tell the customer that; don't request another one.")}
        if existing is not None:                      # shown again: give it a fresh confirmation window
            existing.expires_at = now + _window()
            existing.thread_id = thread_id or existing.thread_id
            if reason:
                existing.reason = reason[:500]
            return _proposal(existing)
        kind = "duplicate payment" if p.status == "Duplicate" else "payment"
        a = PendingAction(
            id=_new_id(), customer_id=customer_id, thread_id=thread_id, kind="refund", subject=txn,
            status="awaiting_customer", summary=f"Refund {money(p.amount)} for {kind} {txn} on order {p.order_id}",
            reason=(reason or "")[:500], amount=p.amount, requires_staff=True, created_at=now,
            expires_at=now + _window(),
            payload=json.dumps({"transaction_id": txn, "payment_id": p.id, "order_id": p.order_id}),
        )
        s.add(a)
        return _proposal(a)


def propose_email_change(customer_id: str, thread_id: Optional[str], new_email: str) -> dict:
    email = (new_email or "").strip().lower()
    if not EMAIL_PATTERN.match(email) or len(email) > 254:
        return {"error": "That isn't a valid email address. Ask the customer for one like name@example.com."}
    now = utcnow_naive()
    with session_scope() as s:
        c = s.get(Customer, customer_id)
        if c is None:
            return {"error": "The customer's account could not be found."}
        if c.email.lower() == email:
            return {"error": "That is already the email address on this account."}
        if s.query(Customer).filter(func.lower(Customer.email) == email, Customer.id != customer_id).first():
            return {"error": "That email address is already used by another account. Ask for a different one."}
        existing = _open_action(s, customer_id, "update_email", "email", now)
        if existing is not None:
            if json.loads(existing.payload).get("new_email") == email and existing.status == "awaiting_customer":
                existing.expires_at = now + _window()
                existing.thread_id = thread_id or existing.thread_id
                return _proposal(existing)
            existing.status, existing.resolved_at, existing.resolved_by = "cancelled", now, "system"
            existing.resolution_note = "Replaced by a newer request."
        a = PendingAction(
            id=_new_id(), customer_id=customer_id, thread_id=thread_id, kind="update_email", subject="email",
            status="awaiting_customer", summary=f"Change your sign-in email from {c.email} to {email}",
            requires_staff=False, created_at=now, expires_at=now + _window(),
            payload=json.dumps({"old_email": c.email, "new_email": email}),
        )
        s.add(a)
        return _proposal(a)


def propose_order_cancellation(customer_id: str, thread_id: Optional[str], order_id: str, reason: str = "") -> dict:
    oid = (order_id or "").strip().upper()
    now = utcnow_naive()
    with session_scope() as s:
        o = s.get(Order, oid)
        if o is None or o.customer_id != customer_id:
            return {"error": f"Order {oid} was not found on this account. Ask the customer to check the ID."}
        if o.status == "Cancelled":
            return {"error": f"Order {oid} is already cancelled."}
        if o.status != "Processing":
            return {"error": f"Order {oid} is {o.status}, so it can't be cancelled. Only orders that are still "
                             "Processing can be cancelled."}
        existing = _open_action(s, customer_id, "cancel_order", oid, now)
        if existing is not None:
            existing.expires_at = now + _window()
            existing.thread_id = thread_id or existing.thread_id
            return _proposal(existing)
        paid = [p for p in o.payments if p.status == "Successful"]
        total = sum(p.amount for p in paid)
        summary = f"Cancel order {oid} ({money(o.amount)})"
        if paid:
            summary += f" and request a refund of {money(total)}"
        a = PendingAction(
            id=_new_id(), customer_id=customer_id, thread_id=thread_id, kind="cancel_order", subject=oid,
            status="awaiting_customer", summary=summary, reason=(reason or "")[:500], amount=o.amount,
            requires_staff=False, created_at=now, expires_at=now + _window(),
            payload=json.dumps({"order_id": oid, "refunds": [p.transaction_id for p in paid]}),
        )
        s.add(a)
        return _proposal(a)


def requests_for_customer(customer_id: str, limit: int = 10) -> List[dict]:
    """The customer's recent requests in plain words, for the assistant's get_my_requests tool."""
    now = utcnow_naive()
    with session_scope() as s:
        rows = (s.query(PendingAction).filter(PendingAction.customer_id == customer_id)
                .order_by(PendingAction.created_at.desc()).limit(limit).all())
        out = []
        for a in rows:
            _expire_if_due(a, now)
            item = {"request_id": a.id, "summary": a.summary, "status": STATUS_TEXT.get(a.status, a.status),
                    "requested_at": iso(a.created_at)}
            if a.status == "rejected" and a.resolution_note:
                item["reason_given"] = a.resolution_note
            if a.status == "failed" and a.result:
                item["problem"] = a.result
            out.append(item)
        return out


# ---------- reading ----------

def _load_for_customer(s, action_id: str, customer_id: str) -> PendingAction:
    a = s.get(PendingAction, action_id)
    if a is None or a.customer_id != customer_id:
        raise ActionNotFound("That request wasn't found on your account.")
    return a


def get_for_customer(action_id: str, customer_id: str) -> dict:
    with session_scope() as s:
        a = _load_for_customer(s, action_id, customer_id)
        _expire_if_due(a, utcnow_naive())
        return action_dict(a)


def actions_by_ids(action_ids: List[str], customer_id: str) -> List[dict]:
    if not action_ids:
        return []
    now = utcnow_naive()
    with session_scope() as s:
        rows = {a.id: a for a in s.query(PendingAction).filter(PendingAction.id.in_(action_ids),
                                                               PendingAction.customer_id == customer_id)}
        out = []
        for action_id in dict.fromkeys(action_ids):
            if action_id in rows:
                _expire_if_due(rows[action_id], now)
                out.append(action_dict(rows[action_id]))
        return out


def actions_for_thread(thread_id: str, customer_id: str) -> List[dict]:
    now = utcnow_naive()
    with session_scope() as s:
        rows = (s.query(PendingAction)
                .filter(PendingAction.thread_id == thread_id, PendingAction.customer_id == customer_id)
                .order_by(PendingAction.created_at).all())
        for a in rows:
            _expire_if_due(a, now)
        return [action_dict(a) for a in rows]


# ---------- carrying out an action ----------

def _do_refund(s, a: PendingAction, now) -> Tuple[str, str]:
    data = json.loads(a.payload)
    p = s.query(Payment).filter_by(transaction_id=data["transaction_id"], customer_id=a.customer_id).first()
    if p is None:
        raise ExecutionFailed("the payment could not be found.")
    if p.status == "Refunded":
        raise ExecutionFailed(f"payment {p.transaction_id} has already been refunded.")
    p.status = "Refunded"
    return (f"Payment {p.transaction_id} marked Refunded ({money(p.amount)}).",
            f"Good news: your refund of {money(p.amount)} for payment {p.transaction_id} has been approved. "
            "It usually reaches your card within 5 to 7 business days.")


def _do_email(s, a: PendingAction, now) -> Tuple[str, str]:
    new = json.loads(a.payload)["new_email"]
    c = s.get(Customer, a.customer_id)
    if c is None:
        raise ExecutionFailed("the account could not be found.")
    if s.query(Customer).filter(func.lower(Customer.email) == new, Customer.id != c.id).first():
        raise ExecutionFailed("that email address is now used by another account.")
    old, c.email = c.email, new
    return (f"Email changed from {old} to {new}.",
            f"Done: your sign-in email is now {new}. Use it the next time you sign in.")


def _do_cancel(s, a: PendingAction, now) -> Tuple[str, str]:
    oid = json.loads(a.payload)["order_id"]
    o = s.get(Order, oid)
    if o is None or o.customer_id != a.customer_id:
        raise ExecutionFailed("the order could not be found.")
    if o.status != "Processing":
        raise ExecutionFailed(f"order {oid} is now {o.status}, so it can no longer be cancelled.")
    o.status = "Cancelled"
    refunds = []
    for p in o.payments:
        if p.status != "Successful" or _open_action(s, a.customer_id, "refund", p.transaction_id, now):
            continue
        r = PendingAction(
            id=_new_id(), customer_id=a.customer_id, thread_id=a.thread_id, kind="refund", subject=p.transaction_id,
            status="awaiting_staff", summary=f"Refund {money(p.amount)} for payment {p.transaction_id} on "
                                             f"cancelled order {oid}",
            reason=f"Order {oid} was cancelled by the customer.", amount=p.amount, requires_staff=True,
            parent_id=a.id, created_at=now, confirmed_at=now,
            payload=json.dumps({"transaction_id": p.transaction_id, "payment_id": p.id, "order_id": oid}),
        )
        s.add(r)
        refunds.append(r)
    result = f"Order {oid} cancelled."
    message = f"Done: order {oid} is cancelled."
    if refunds:
        result += " Refund requests sent to billing: " + ", ".join(r.id for r in refunds) + "."
        amounts = " and ".join(f"{money(r.amount)} for payment {r.subject}" for r in refunds)
        message += (f" A refund of {amounts} is now with our billing team for approval; they review "
                    "requests within 1 business day.")
    return result, message


EXECUTORS = {"refund": _do_refund, "update_email": _do_email, "cancel_order": _do_cancel}


def _execute(s, a: PendingAction, now, resolved_by: str, author: Optional[str] = None,
             note: Optional[str] = None, done_kind: str = "action_completed") -> list:
    """Run the action (status is already 'processing') and record the outcome. Returns the chat updates."""
    try:
        result, message = EXECUTORS[a.kind](s, a, now)
    except ExecutionFailed as exc:   # executors check everything before changing anything
        a.status, a.result, a.resolved_at, a.resolved_by = "failed", str(exc), now, resolved_by
        logger.warning("Action %s failed: %s", a.id, exc)
        return [add_update(s, a.thread_id, a.customer_id, "action_failed",
                           f"We couldn't complete this request ({_lower_first(a.summary)}): {exc} "
                           "Nothing was changed.", action_id=a.id)]
    a.status, a.result, a.resolved_at, a.resolved_by = "completed", result, now, resolved_by
    if note:
        a.resolution_note = note
        message += f"\n\nNote from {author.split()[0] if author else 'the support team'}: {_sentence(note)}"
    logger.info("Action %s completed by %s: %s", a.id, resolved_by, result)
    return [add_update(s, a.thread_id, a.customer_id, done_kind, message, author=author, action_id=a.id)]


# ---------- customer decisions ----------

def confirm(action_id: str, customer_id: str) -> Tuple[dict, List[dict]]:
    """The customer pressed Confirm. Refunds go to staff; everything else happens now."""
    now = utcnow_naive()
    with session_scope() as s:
        a = _load_for_customer(s, action_id, customer_id)
        if _expire_if_due(a, now):
            raise ActionExpired("This request expired before it was confirmed. Ask the assistant again if you "
                                "still need it.")
        if a.status != "awaiting_customer":
            raise ActionConflict(f"This request can't be confirmed: {STATUS_TEXT.get(a.status, a.status).lower()}.")
        next_status = "awaiting_staff" if a.requires_staff else "processing"
        if not _claim(s, a.id, "awaiting_customer", next_status, confirmed_at=now):
            raise ActionConflict("This request was changed a moment ago. Refresh to see where it stands.")
        s.refresh(a)
        if a.requires_staff:
            updates = [add_update(s, a.thread_id, customer_id, "action_submitted",
                                  f"You confirmed: {_lower_first(a.summary)}. It's now with our billing team, who "
                                  "review requests within 1 business day.", action_id=a.id)]
        else:
            updates = _execute(s, a, now, resolved_by="customer")
        return action_dict(a), update_dicts(updates)


def cancel(action_id: str, customer_id: str) -> Tuple[dict, List[dict]]:
    """The customer pressed Cancel (or withdrew a request still waiting for staff)."""
    now = utcnow_naive()
    with session_scope() as s:
        a = _load_for_customer(s, action_id, customer_id)
        _expire_if_due(a, now)
        if a.status not in OPEN_STATUSES:
            raise ActionConflict(f"This request can't be cancelled: {STATUS_TEXT.get(a.status, a.status).lower()}.")
        if not _claim(s, a.id, a.status, "cancelled", resolved_at=now, resolved_by="customer"):
            raise ActionConflict("This request was changed a moment ago. Refresh to see where it stands.")
        s.refresh(a)
        updates = [add_update(s, a.thread_id, customer_id, "action_cancelled",
                              f"You cancelled this request: {_lower_first(a.summary)}. Nothing was changed.",
                              action_id=a.id)]
        return action_dict(a), update_dicts(updates)


# ---------- staff decisions ----------

def _load_for_staff(s, action_id: str, staff: AuthUser) -> PendingAction:
    if staff.role not in STAFF_DECIDES:
        raise ActionNotFound("Request not found.")
    a = s.get(PendingAction, action_id)
    if a is None or not a.requires_staff:
        raise ActionNotFound("Request not found.")
    return a


def approve(action_id: str, staff: AuthUser, note: str = "") -> Tuple[dict, List[dict]]:
    now = utcnow_naive()
    note = " ".join((note or "").split())[:1000]
    with session_scope() as s:
        a = _load_for_staff(s, action_id, staff)
        if a.status != "awaiting_staff":
            raise ActionConflict(f"This request is no longer waiting for approval "
                                 f"({STATUS_TEXT.get(a.status, a.status).lower()}).")
        if not _claim(s, a.id, "awaiting_staff", "processing"):
            raise ActionConflict("Someone else handled this request a moment ago.")
        s.refresh(a)
        updates = _execute(s, a, now, resolved_by=staff.id, author=staff.name, note=note or None,
                           done_kind="staff_approved")
        return action_dict(a), update_dicts(updates)


def reject(action_id: str, staff: AuthUser, reason: str) -> Tuple[dict, List[dict]]:
    now = utcnow_naive()
    reason = " ".join((reason or "").split())[:1000]
    if len(reason) < 3:
        raise InvalidDecision("Give a reason for the customer.")
    with session_scope() as s:
        a = _load_for_staff(s, action_id, staff)
        if a.status != "awaiting_staff":
            raise ActionConflict(f"This request is no longer waiting for approval "
                                 f"({STATUS_TEXT.get(a.status, a.status).lower()}).")
        if not _claim(s, a.id, "awaiting_staff", "rejected", resolved_at=now, resolved_by=staff.id,
                      resolution_note=reason):
            raise ActionConflict("Someone else handled this request a moment ago.")
        s.refresh(a)
        updates = [add_update(s, a.thread_id, a.customer_id, "staff_rejected",
                              f"We've reviewed your request ({_lower_first(a.summary)}) and can't approve it. "
                              f"Reason: {_sentence(reason)} If you have questions, just reply here.",
                              author=staff.name, action_id=a.id)]
        logger.info("Action %s rejected by %s", a.id, staff.id)
        return action_dict(a), update_dicts(updates)
