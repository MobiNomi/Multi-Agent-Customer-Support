"""Guardrail notes: what fired, in words a customer or reviewer understands, plus the event log."""
import logging
from typing import Dict, List, Optional

from app.database.connection import session_scope
from app.database.models import GuardrailEvent

logger = logging.getLogger(__name__)

MESSAGES = {
    "card_masked": "A card number was hidden before the message was sent to the AI or saved.",
    "cnic_masked": "A CNIC number was hidden before the message was sent to the AI or saved.",
    "password_masked": "A password was hidden before the message was sent to the AI or saved.",
    "cvv_masked": "A card security code was hidden before the message was sent to the AI or saved.",
    "iban_masked": "A bank account number (IBAN) was hidden before the message was sent to the AI or saved.",
    "injection_blocked": "The message looked like an attempt to change how the assistant works, so it was "
                         "stopped before reaching the AI.",
    "off_topic": "The request isn't about AcmeCloud, so no agent was used.",
    "unsafe": "The request is something the assistant can't help with, so no agent was used.",
    "ticket_reused": "A ticket on the same topic was opened in the last hour, so it was reused instead of "
                     "opening another.",
    "ticket_limit": "The hourly ticket limit was reached, so no new ticket was opened.",
    "citation_removed": "A claim citing a document the agent never actually read was removed.",
    "promise_rewritten": "A sentence saying a refund was already done was replaced: the assistant can only "
                         "open refund requests.",
    "leak_blocked": "The reply repeated the assistant's internal instructions, so it was replaced.",
    "internal_names_replaced": "Internal tool names in the reply were replaced with plain words.",
    "reply_masked": "Sensitive numbers in the reply were hidden.",
}
STAGES = {"card_masked": "input", "cnic_masked": "input", "password_masked": "input", "cvv_masked": "input",
          "iban_masked": "input", "injection_blocked": "input", "off_topic": "router", "unsafe": "router",
          "ticket_reused": "action", "ticket_limit": "action"}


def note(kind: str) -> Dict[str, str]:
    return {"stage": STAGES.get(kind, "output"), "kind": kind, "message": MESSAGES[kind]}


def notes(kinds: List[str]) -> List[Dict[str, str]]:
    return [note(k) for k in dict.fromkeys(kinds)]   # unique, order kept


def record(kinds: List[str], customer_id: Optional[str], thread_id: Optional[str] = None,
           detail: str = "") -> None:
    """Write one event row per kind. Never stores the sensitive text itself."""
    if not kinds:
        return
    try:
        with session_scope() as s:
            for kind in dict.fromkeys(kinds):
                s.add(GuardrailEvent(customer_id=customer_id, thread_id=thread_id,
                                     stage=STAGES.get(kind, "output"), kind=kind, detail=detail[:300]))
    except Exception:  # logging must never break a chat
        logger.exception("Could not record guardrail events %s", kinds)
