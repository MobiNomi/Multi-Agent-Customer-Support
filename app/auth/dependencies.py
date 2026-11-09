"""FastAPI dependency: who is making this request?

The token is read from the HttpOnly session cookie set at sign-in (used by the chat page), or from
an `Authorization: Bearer <token>` header (used by API clients, curl and the /docs page).
"""
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import AuthUser, user_for_token
from app.auth.tokens import InvalidToken, decode_access_token

COOKIE_NAME = "acme_session"
_bearer = HTTPBearer(auto_error=False, description="Token from POST /api/v1/auth/login")


def get_current_user(request: Request,
                     credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> AuthUser:
    token = credentials.credentials if credentials else request.cookies.get(COOKIE_NAME)
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
