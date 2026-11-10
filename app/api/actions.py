"""Customer endpoints for proposed actions: see one, confirm it, cancel it, and poll for updates."""
from fastapi import APIRouter, Depends, Query

from app.actions import service
from app.actions.updates import updates_for_thread
from app.api.conversation import thread_id_for
from app.api.schemas import ActionOut, ActionResult, SessionUpdates
from app.auth.dependencies import get_current_customer
from app.auth.service import AuthUser

router = APIRouter(prefix="/api/v1", tags=["actions"])


@router.get("/actions/{action_id}", response_model=ActionOut)
def get_action(action_id: str, user: AuthUser = Depends(get_current_customer)):
    """One of the signed-in customer's requests."""
    return service.get_for_customer(action_id, user.id)


@router.post("/actions/{action_id}/confirm", response_model=ActionResult)
def confirm_action(action_id: str, user: AuthUser = Depends(get_current_customer)):
    """The customer agrees. Refunds go to the billing team; email changes and cancellations happen now.

    409 if it was already confirmed, cancelled or decided; 410 if it expired before being confirmed.
    """
    action, updates = service.confirm(action_id, user.id)
    return ActionResult(action=action, updates=updates)


@router.post("/actions/{action_id}/cancel", response_model=ActionResult)
def cancel_action(action_id: str, user: AuthUser = Depends(get_current_customer)):
    """The customer says no, or withdraws a refund request that staff haven't decided yet."""
    action, updates = service.cancel(action_id, user.id)
    return ActionResult(action=action, updates=updates)


@router.get("/sessions/{session_id}/updates", response_model=SessionUpdates)
def session_updates(session_id: str, after: int = Query(0, ge=0),
                    user: AuthUser = Depends(get_current_customer)):
    """Polled by the chat page: support messages newer than `after`, and the current state of every request."""
    thread_id = thread_id_for(user.id, session_id)
    updates = updates_for_thread(thread_id, after_id=after)
    last = updates[-1]["id"] if updates else after
    return SessionUpdates(updates=updates, actions=service.actions_for_thread(thread_id, user.id),
                          last_update_id=last)
