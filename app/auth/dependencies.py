"""FastAPI dependencies: who is making this request, and are they allowed here?

The token is read from an HttpOnly session cookie set at sign-in (used by the web pages), or from an
`Authorization: Bearer <token>` header (used by API clients, curl and the /docs page).

Customers and staff have separate cookies, so one browser can have the customer chat and the staff
console open side by side (handy for demos) without one sign-in replacing the other.
"""
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import AuthUser, user_for_token
from app.auth.tokens import InvalidToken, decode_access_token

COOKIE_NAME = "acme_session"            # customer chat
STAFF_COOKIE_NAME = "acme_staff_session"  # staff console
_bearer = HTTPBearer(auto_error=False, description="Token from POST /api/v1/auth/login or /api/v1/staff/login")


def _user(token: Optional[str]) -> AuthUser:
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        user = user_for_token(decode_access_token(token))
    except InvalidToken:
        user = None
    if user is None:
        raise HTTPException(status_code=401, detail="Your session has ended. Sign in again.",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def get_current_customer(request: Request,
                         credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> AuthUser:
    """The signed-in customer. Staff accounts get 403: they work in the staff console."""
    user = _user(credentials.credentials if credentials else request.cookies.get(COOKIE_NAME))
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Staff accounts can't use the customer chat. "
                                                    "Open the staff console at /staff.")
    return user


# The customer pages and older code call it this.
get_current_user = get_current_customer


def get_current_staff(request: Request,
                      credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> AuthUser:
    """The signed-in staff member. Customers get 403."""
    user = _user(credentials.credentials if credentials else request.cookies.get(STAFF_COOKIE_NAME))
    if user.role != "staff":
        raise HTTPException(status_code=403, detail="This account doesn't have access to the staff console.")
    return user
