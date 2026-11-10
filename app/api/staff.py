"""Staff console API: sign in, see the queue, approve or reject refunds, reply to and close tickets."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.actions import service as actions
from app.api.conversation import thread_messages
from app.api.schemas import (ActionDetail, ApproveRequest, CloseRequest, RejectRequest, ReplyRequest, StaffAccount,
                             StaffDecision, StaffLoginRequest, StaffLoginResponse, StaffQueue, TicketDetail, TicketResult)
from app.auth.dependencies import STAFF_COOKIE_NAME, get_current_staff
from app.auth.service import AccountLocked, AuthUser, LoginFailed, authenticate
from app.auth.session import start_session
from app.staff import service as staff_service

router = APIRouter(prefix="/api/v1/staff", tags=["staff console"])


def _staff_account(user: AuthUser) -> StaffAccount:
    return StaffAccount(id=user.id, name=user.name, email=user.email)


@router.post("/login", response_model=StaffLoginResponse)
def staff_login(body: StaffLoginRequest, response: Response):
    """Staff sign-in. Sets a separate cookie, so the customer chat and the console can be open together."""
    try:
        user, token_version = authenticate(body.email, body.password)
    except AccountLocked as exc:
        minutes = max(1, round(exc.retry_after_seconds / 60))
        raise HTTPException(status_code=429, detail=f"Too many failed sign-in attempts. Try again in {minutes} "
                                                    f"minute{'s' if minutes != 1 else ''}.",
                            headers={"Retry-After": str(exc.retry_after_seconds)})
    except LoginFailed:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    if user.role != "staff":
        raise HTTPException(status_code=403, detail="This account doesn't have access to the staff console.")
    token, expires = start_session(response, user, token_version, STAFF_COOKIE_NAME)
    return StaffLoginResponse(staff=_staff_account(user), access_token=token, expires_at=expires)


@router.post("/logout", status_code=204)
def staff_logout():
    response = Response(status_code=204)
    response.delete_cookie(STAFF_COOKIE_NAME, path="/")
    return response


@router.get("/me", response_model=StaffAccount)
def staff_me(user: AuthUser = Depends(get_current_staff)):
    return _staff_account(user)


@router.get("/queue", response_model=StaffQueue)
def staff_queue(user: AuthUser = Depends(get_current_staff)):
    """Refunds waiting for approval, open tickets, and the latest decisions."""
    return staff_service.queue()


@router.get("/actions/{action_id}", response_model=ActionDetail)
def action_detail(action_id: str, request: Request, user: AuthUser = Depends(get_current_staff)):
    """One request with the customer, the payment, its order and the conversation it came from."""
    detail = staff_service.action_detail(action_id)
    detail["conversation"] = thread_messages(request.app.state.graph, detail.get("thread_id"))[0]
    return detail


@router.post("/actions/{action_id}/approve", response_model=StaffDecision)
def approve_action(action_id: str, body: ApproveRequest, user: AuthUser = Depends(get_current_staff)):
    """Approve and carry out the request. The customer sees the outcome in their chat. 409 if already decided."""
    action, updates = actions.approve(action_id, user, body.note)
    return StaffDecision(action=action, updates=updates, delivered_to_chat=bool(updates))


@router.post("/actions/{action_id}/reject", response_model=StaffDecision)
def reject_action(action_id: str, body: RejectRequest, user: AuthUser = Depends(get_current_staff)):
    """Decline the request. The reason is sent to the customer. 422 without a reason, 409 if already decided."""
    action, updates = actions.reject(action_id, user, body.reason)
    return StaffDecision(action=action, updates=updates, delivered_to_chat=bool(updates))


@router.get("/tickets/{ticket_id}", response_model=TicketDetail)
def ticket_detail(ticket_id: str, request: Request, user: AuthUser = Depends(get_current_staff)):
    detail = staff_service.ticket_detail(ticket_id)
    detail["conversation"] = thread_messages(request.app.state.graph, detail.get("thread_id"))[0]
    return detail


@router.post("/tickets/{ticket_id}/reply", response_model=TicketResult)
def reply_to_ticket(ticket_id: str, body: ReplyRequest, request: Request,
                    user: AuthUser = Depends(get_current_staff)):
    """Save a reply on the ticket and send it to the customer's chat."""
    detail, updates = staff_service.reply(ticket_id, user, body.message)
    detail["conversation"] = thread_messages(request.app.state.graph, detail.get("thread_id"))[0]
    return TicketResult(ticket=detail, delivered_to_chat=bool(updates))


@router.post("/tickets/{ticket_id}/close", response_model=TicketResult)
def close_ticket(ticket_id: str, body: CloseRequest, request: Request,
                 user: AuthUser = Depends(get_current_staff)):
    detail, updates = staff_service.close(ticket_id, user, body.message)
    detail["conversation"] = thread_messages(request.app.state.graph, detail.get("thread_id"))[0]
    return TicketResult(ticket=detail, delivered_to_chat=bool(updates))
