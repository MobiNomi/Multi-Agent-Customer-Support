"""Messages for the customer's chat that don't come from the assistant: staff decisions, staff replies,
and the outcome of actions the customer confirmed.

They are stored in the database (not written straight into the conversation memory), because a staff
member may decide hours later while the customer is mid-conversation. The chat page polls for them, and
they are copied into the conversation memory just before the customer's next message, so the assistant
knows what happened.
"""
from datetime import timezone
from typing import Iterable, List, Optional

from langchain_core.messages import AIMessage

from app.database.connection import session_scope
from app.database.models import ConversationUpdate, utcnow_naive

SUPPORT_MESSAGE_NAME = "support"   # AIMessage.name used for these updates inside the conversation memory


def iso(dt) -> Optional[str]:
    """Datetimes are stored as naive UTC; send them with an explicit Z so browsers don't read them as local."""
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _as_dict(u: ConversationUpdate) -> dict:
    return {"id": u.id, "kind": u.kind, "author": u.author, "content": u.body, "created_at": iso(u.created_at),
            "action_id": u.action_id, "ticket_id": u.ticket_id}


def add_update(session, thread_id: Optional[str], customer_id: str, kind: str, body: str,
               author: Optional[str] = None, action_id: Optional[str] = None,
               ticket_id: Optional[str] = None) -> Optional[ConversationUpdate]:
    """Queue a message for a chat, inside the caller's transaction. No chat (thread_id None) -> nothing."""
    if not thread_id:
        return None
    update = ConversationUpdate(thread_id=thread_id, customer_id=customer_id, kind=kind, body=body,
                                author=author, action_id=action_id, ticket_id=ticket_id,
                                created_at=utcnow_naive())
    session.add(update)
    session.flush()
    return update


def updates_for_thread(thread_id: str, after_id: int = 0) -> List[dict]:
    with session_scope() as session:
        rows = (session.query(ConversationUpdate)
                .filter(ConversationUpdate.thread_id == thread_id, ConversationUpdate.id > after_id)
                .order_by(ConversationUpdate.id).all())
        return [_as_dict(u) for u in rows]


def update_dicts(updates: Iterable[ConversationUpdate]) -> List[dict]:
    return [_as_dict(u) for u in updates if u is not None]


def as_message(update: dict) -> AIMessage:
    """The update as it is kept in the conversation memory. A fixed id makes adding it twice harmless."""
    return AIMessage(
        content=update["content"],
        name=SUPPORT_MESSAGE_NAME,
        id=f"update-{update['id']}",
        additional_kwargs={"update_id": update["id"], "kind": update["kind"], "author": update["author"],
                           "created_at": update["created_at"], "action_id": update.get("action_id")},
    )


def update_ids_in(messages) -> set:
    return {m.additional_kwargs.get("update_id") for m in messages
            if isinstance(m, AIMessage) and m.name == SUPPORT_MESSAGE_NAME}


def missing_updates(thread_id: str, messages) -> List[dict]:
    """Updates for this chat that aren't in the conversation memory yet, oldest first."""
    present = update_ids_in(messages)
    return [u for u in updates_for_thread(thread_id) if u["id"] not in present]
