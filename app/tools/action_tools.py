"""Action tools: the assistant can *propose* account changes, never make them.

Each call only creates a pending request. The customer sees Confirm / Cancel buttons under the reply, and
refunds also need approval from billing staff in the staff console. `customer_id` and `thread_id` are
injected by the tool executor from the signed-in session; the model never sees or sets them.
"""
from typing import Annotated, Any, Dict, Optional

from langchain_core.tools import InjectedToolArg, tool

from app.actions.service import (propose_email_change, propose_order_cancellation, propose_refund,
                                 requests_for_customer)

ACTION_TOOL_NAMES = ("request_refund", "update_email", "cancel_order")


@tool
def request_refund(
    transaction_id: str,
    customer_id: Annotated[str, InjectedToolArg],
    reason: str = "",
    thread_id: Annotated[Optional[str], InjectedToolArg] = None,
) -> Dict[str, Any]:
    """Prepare a refund request for one of the current customer's payments, by transaction ID (e.g. TXN-8922).
    Nothing is refunded by this call: the customer must confirm it, then the billing team approves it."""
    return propose_refund(customer_id, thread_id, transaction_id, reason)


@tool
def update_email(
    new_email: str,
    customer_id: Annotated[str, InjectedToolArg],
    thread_id: Annotated[Optional[str], InjectedToolArg] = None,
) -> Dict[str, Any]:
    """Prepare a change of the email address the current customer signs in with.
    Nothing changes until the customer confirms it."""
    return propose_email_change(customer_id, thread_id, new_email)


@tool
def cancel_order(
    order_id: str,
    customer_id: Annotated[str, InjectedToolArg],
    reason: str = "",
    thread_id: Annotated[Optional[str], InjectedToolArg] = None,
) -> Dict[str, Any]:
    """Prepare the cancellation of one of the current customer's orders that is still Processing (e.g. ORD-1035).
    Nothing changes until the customer confirms it."""
    return propose_order_cancellation(customer_id, thread_id, order_id, reason)


@tool
def get_my_requests(customer_id: Annotated[str, InjectedToolArg]) -> Dict[str, Any]:
    """List the current customer's recent requests (refunds, email changes, cancellations) and where each stands."""
    items = requests_for_customer(customer_id)
    return {"requests": items} if items else {"requests": [], "note": "The customer has no requests yet."}
