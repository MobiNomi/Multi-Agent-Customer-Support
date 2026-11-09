"""Sign-in rules: check the password, lock the account after too many failures, load the customer."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import re

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import MIN_PASSWORD_LENGTH, burn_verify_time, hash_password, verify_password
from app.config import get_settings
from app.database.connection import session_scope
from app.database.models import Customer


@dataclass(frozen=True)
class AuthUser:
    id: str
    name: str
    email: str
    plan: str
    role: str
    orders: List[str] = field(default_factory=list)


class LoginFailed(Exception):
    """Wrong email or password. Deliberately doesn't say which."""


class EmailTaken(Exception):
    """An account with this email already exists."""


class AccountLocked(Exception):
    def __init__(self, retry_after_seconds: int):
        super().__init__(f"locked for {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, as stored in the database


def _to_user(c: Customer) -> AuthUser:
    return AuthUser(id=c.id, name=c.name, email=c.email, plan=c.plan, role=c.role or "customer",
                    orders=[o.id for o in c.orders])


def _find_by_email(session, email: str) -> Optional[Customer]:
    return session.query(Customer).filter(func.lower(Customer.email) == email.strip().lower()).first()


def authenticate(email: str, password: str) -> Tuple[AuthUser, int]:
    """Return (customer, token_version) or raise LoginFailed / AccountLocked."""
    s = get_settings()
    outcome, user, version, retry_after = "failed", None, 0, 0

    with session_scope() as session:
        customer = _find_by_email(session, email)
        if customer is None or not customer.password_hash:
            burn_verify_time(password)
        else:
            now = _now()
            if customer.locked_until and customer.locked_until > now:
                outcome, retry_after = "locked", int((customer.locked_until - now).total_seconds()) + 1
            elif verify_password(password, customer.password_hash):
                customer.failed_logins = 0
                customer.locked_until = None
                customer.last_login_at = now
                outcome, user, version = "ok", _to_user(customer), customer.token_version or 0
            else:
                customer.failed_logins = (customer.failed_logins or 0) + 1
                if customer.failed_logins >= s.login_max_attempts:
                    customer.failed_logins = 0
                    customer.locked_until = now + timedelta(minutes=s.login_lock_minutes)
                    outcome, retry_after = "locked", s.login_lock_minutes * 60
    # Raise only after the session has committed the counters (raising inside would roll them back).
    if outcome == "ok":
        return user, version
    if outcome == "locked":
        raise AccountLocked(retry_after)
    raise LoginFailed()


def user_for_token(claims: dict) -> Optional[AuthUser]:
    """The customer a valid token belongs to, or None if they no longer exist or the token was revoked."""
    with session_scope() as session:
        customer = session.get(Customer, claims.get("sub"))
        if customer is None or (customer.token_version or 0) != claims.get("ver"):
            return None
        return _to_user(customer)


def set_password(email: str, new_password: str) -> str:
    """Give a customer a new password. Signs them out everywhere. Returns the customer ID."""
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    with session_scope() as session:
        customer = _find_by_email(session, email)
        if customer is None:
            raise LookupError(f"No customer with email {email}")
        customer.password_hash = hash_password(new_password)
        customer.token_version = (customer.token_version or 0) + 1
        customer.failed_logins = 0
        customer.locked_until = None
        return customer.id


# ---------- registration ----------

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COMMON_PASSWORDS = frozenset("""
password password1 password123 12345678 123456789 1234567890 11111111 00000000 qwerty123 qwertyuiop
iloveyou abc12345 letmein1 welcome1 admin123 pakistan pakistan1 pakistan123 87654321 asdfghjk
""".split())
NEW_CUSTOMER_PLAN = "Free"


def validate_registration(name: str, email: str, password: str) -> tuple:
    """Clean the inputs and check the rules. Returns (name, email) or raises ValueError with a message for the customer."""
    name = " ".join(name.split())
    email = email.strip().lower()
    if not name:
        raise ValueError("Enter your name.")
    if len(name) > 100:
        raise ValueError("Use a name of 100 characters or fewer.")
    if not EMAIL_PATTERN.match(email) or len(email) > 254:
        raise ValueError("Enter an email address like name@example.com.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters.")
    if password.lower() in COMMON_PASSWORDS or password.lower() == email.split("@")[0]:
        raise ValueError("That password is too easy to guess. Choose a less common one.")
    return name, email


def _next_customer_id(session) -> str:
    numbers = [int(cid[4:]) for (cid,) in session.query(Customer.id).filter(Customer.id.like("CUS-%"))
               if cid[4:].isdigit()]
    return f"CUS-{max(numbers, default=1000) + 1}"


def register_customer(name: str, email: str, password: str) -> Tuple[AuthUser, int]:
    """Create a customer who can sign in straight away. Raises ValueError or EmailTaken."""
    name, email = validate_registration(name, email, password)
    password_hash = hash_password(password)
    for _ in range(3):  # two sign-ups at the same moment can pick the same ID; try again
        try:
            with session_scope() as session:
                if _find_by_email(session, email) is not None:
                    raise EmailTaken()
                customer = Customer(id=_next_customer_id(session), name=name, email=email,
                                    plan=NEW_CUSTOMER_PLAN, password_hash=password_hash,
                                    role="customer", last_login_at=_now())
                session.add(customer)
                session.flush()
                return _to_user(customer), 0
        except IntegrityError:
            with session_scope() as session:   # the email index caught a duplicate sign-up
                if _find_by_email(session, email) is not None:
                    raise EmailTaken()
    raise RuntimeError("Could not create the account. Try again.")
