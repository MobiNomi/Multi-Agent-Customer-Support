"""Reading a conversation back: the assistant's memory plus support updates not yet copied into it."""
from typing import List, Optional, Tuple

from app.actions.service import actions_by_ids
from app.actions.updates import missing_updates
from app.graph.history import conversation_log


def thread_id_for(customer_id: str, session_id: str) -> str:
    # Thread is bound to the customer, so a leaked session_id can't open someone else's chat.
    return f"{customer_id}:{session_id}"


def thread_messages(graph, thread_id: Optional[str]) -> Tuple[List[dict], int]:
    """The whole chat as the customer sees it, and the highest support update ID in it."""
    if not thread_id:
        return [], 0
    state = graph.get_state({"configurable": {"thread_id": thread_id}})
    messages = state.values.get("messages", []) if state and state.values else []
    log = conversation_log(messages, with_extras=True)
    for u in missing_updates(thread_id, messages):
        log.append({"role": "support", "content": u["content"], "update_id": u["id"], "kind": u["kind"],
                    "author": u["author"], "created_at": u["created_at"]})
    last = max((m.get("update_id") or 0 for m in log if m["role"] == "support"), default=0)
    return log, last


def with_actions(log: List[dict], customer_id: str) -> List[dict]:
    """Replace each reply's action IDs with the actions themselves, as they stand now."""
    ids = [a for m in log for a in m.get("action_ids", [])]
    actions = {a["id"]: a for a in actions_by_ids(ids, customer_id)}
    out = []
    for m in log:
        m = dict(m)
        if "action_ids" in m:
            m["actions"] = [actions[a] for a in m.pop("action_ids") if a in actions]
        out.append(m)
    return out
