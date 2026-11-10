"""Starting a signed-in session: create the token and set it as an HttpOnly cookie."""
from datetime import datetime
from typing import Tuple

from fastapi import Response

from app.auth.service import AuthUser
from app.auth.tokens import create_access_token
from app.config import get_settings


def start_session(response: Response, user: AuthUser, token_version: int, cookie_name: str) -> Tuple[str, datetime]:
    settings = get_settings()
    token, expires = create_access_token(user.id, token_version, user.role)
    response.set_cookie(cookie_name, token, max_age=settings.access_token_minutes * 60,
                        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    return token, expires
