"""Signed session tokens (JWT).

After a successful sign-in the server hands out a token that says "this is customer CUS-1001,
valid until 18:00", signed with AUTH_SECRET. Anyone can read a token, but changing it breaks the
signature, so the server can trust what's inside without looking anything up.
"""
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)
ALGORITHM = "HS256"
_temporary_secret: Optional[str] = None


class InvalidToken(Exception):
    """Missing, expired, tampered with, or signed with a different secret."""


def _secret() -> str:
    configured = get_settings().auth_secret
    if configured:
        return configured
    global _temporary_secret
    if _temporary_secret is None:
        _temporary_secret = secrets.token_urlsafe(48)
        logger.warning("AUTH_SECRET is not set: using a temporary secret, so everyone is signed out "
                       "whenever the server restarts. Set AUTH_SECRET in .env.")
    return _temporary_secret


def create_access_token(customer_id: str, token_version: int, role: str = "customer",
                        minutes: Optional[int] = None) -> Tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=get_settings().access_token_minutes if minutes is None else minutes)
    payload = {
        "sub": customer_id,
        "ver": token_version,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM), expires


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM],
                          options={"require": ["sub", "exp", "iat", "ver"]})
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
