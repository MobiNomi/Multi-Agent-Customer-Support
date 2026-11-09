from datetime import timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import config
from app.auth import tokens
from app.auth.passwords import hash_password, verify_password
from app.auth.service import _now, set_password
from app.auth.tokens import create_access_token
from app.database.connection import session_scope
from app.database.migrations import ensure_schema
from app.database.models import Customer
from app.database.seed import ensure_demo_passwords
from app.main import app
from tests.helpers import ALI, DEMO_PASSWORD, sign_in


@pytest.fixture
def anon():
    with TestClient(app) as c:
        yield c


def login(client, email=ALI, password=DEMO_PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ---------- passwords ----------

def test_password_hash_roundtrip_and_unique_salts():
    a, b = hash_password("correct horse"), hash_password("correct horse")
    assert a != b and a.startswith("$argon2")
    assert verify_password("correct horse", a) and not verify_password("wrong", a)
    assert not verify_password("anything", "not-a-hash")


# ---------- signing in ----------

def test_sign_in_sets_secure_session_cookie(anon):
    res = login(anon)
    assert res.status_code == 200
    body = res.json()
    assert body["customer"] == {"id": "CUS-1001", "name": "Ali Khan", "email": "ali@example.com",
                                "plan": "Pro", "orders": ["ORD-1034"]}
    assert body["token_type"] == "bearer" and body["access_token"]
    cookie = res.headers["set-cookie"].lower()
    assert "acme_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert anon.get("/api/v1/auth/me").json()["id"] == "CUS-1001"


def test_email_is_case_and_space_insensitive(anon):
    assert login(anon, email="  ALI@Example.COM ").status_code == 200


def test_wrong_password_and_unknown_email_look_the_same(anon):
    wrong_pw, unknown = login(anon, password="nope"), login(anon, email="nobody@example.com")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json() == {"detail": "Email or password is incorrect."}


def test_customer_without_password_cannot_sign_in(anon):
    with session_scope() as s:
        s.get(Customer, "CUS-1002").password_hash = None
    assert login(anon, email="sara@example.com").status_code == 401


def test_lockout_after_five_failures(anon):
    codes = [login(anon, password="wrong").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]
    locked = login(anon)                       # even the right password is refused while locked
    assert locked.status_code == 429
    assert "Try again in 15 minutes" in locked.json()["detail"]
    assert int(locked.headers["retry-after"]) > 800


def test_lock_expires(anon):
    for _ in range(5):
        login(anon, password="wrong")
    with session_scope() as s:
        s.get(Customer, "CUS-1001").locked_until = _now() - timedelta(seconds=1)
    assert login(anon).status_code == 200
    with session_scope() as s:
        c = s.get(Customer, "CUS-1001")
        assert c.locked_until is None and c.failed_logins == 0 and c.last_login_at is not None


def test_success_resets_failure_count(anon):
    for _ in range(4):
        login(anon, password="wrong")
    assert login(anon).status_code == 200
    for _ in range(4):                         # four more failures still don't lock
        assert login(anon, password="wrong").status_code == 401


# ---------- tokens ----------

def test_bearer_token_works_without_cookie(anon):
    token = login(anon).json()["access_token"]
    anon.cookies.clear()
    assert anon.get("/api/v1/auth/me").status_code == 401
    assert anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["id"] == "CUS-1001"


@pytest.mark.parametrize("make_token", [
    lambda: create_access_token("CUS-1001", 0, minutes=-1)[0],                         # expired
    lambda: jwt.encode({"sub": "CUS-1001", "ver": 0, "iat": 0, "exp": 9999999999},
                       "some-other-secret-" + "y" * 40, algorithm="HS256"),            # wrong secret
    lambda: create_access_token("CUS-1001", 0)[0][:-3] + "abc",                        # tampered
    lambda: create_access_token("CUS-9999", 0)[0],                                     # no such customer
    lambda: "not-a-token",
])
def test_bad_tokens_are_rejected(anon, make_token):
    res = anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {make_token()}"})
    assert res.status_code == 401


def test_cannot_forge_another_customer_without_the_secret(anon):
    token = login(anon).json()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["sub"] = "CUS-1002"
    forged = jwt.encode(claims, "guessed-secret-" + "z" * 40, algorithm="HS256")
    anon.cookies.clear()
    assert anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_sign_out_clears_the_session(anon):
    sign_in(anon)
    assert anon.post("/api/v1/auth/logout").status_code == 204
    assert anon.get("/api/v1/auth/me").status_code == 401
    assert anon.post("/api/v1/chat", json={"message": "hi"}).status_code == 401


def test_new_password_signs_out_everywhere(anon):
    token = login(anon).json()["access_token"]
    set_password(ALI, "a-brand-new-password")
    assert anon.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert login(anon).status_code == 401
    assert login(anon, password="a-brand-new-password").status_code == 200


def test_set_password_rules():
    with pytest.raises(ValueError):
        set_password(ALI, "short")
    with pytest.raises(LookupError):
        set_password("nobody@example.com", "long-enough-password")


def test_missing_secret_falls_back_to_temporary_one(anon, monkeypatch):
    monkeypatch.delenv("AUTH_SECRET")
    config.get_settings.cache_clear()
    monkeypatch.setattr(tokens, "_temporary_secret", None)
    assert login(anon).status_code == 200
    assert anon.get("/api/v1/auth/me").status_code == 200
    assert tokens._temporary_secret


def test_secure_cookie_setting(anon, monkeypatch):
    monkeypatch.setenv("COOKIE_SECURE", "true")
    config.get_settings.cache_clear()
    assert "secure" in login(anon).headers["set-cookie"].lower()


# ---------- upgrading an old database ----------

def test_old_database_gets_login_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE customers (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, "
                          "email VARCHAR NOT NULL, plan VARCHAR, created_at DATETIME)"))
        conn.execute(text("INSERT INTO customers VALUES ('CUS-1','A','a@example.com','Pro',NULL)"))
    added = ensure_schema(engine)
    assert set(added) == {"password_hash", "role", "failed_logins", "locked_until", "last_login_at", "token_version"}
    assert ensure_schema(engine) == []          # running again changes nothing
    with engine.connect() as conn:
        row = conn.execute(text("SELECT role, failed_logins, token_version FROM customers")).one()
    assert tuple(row) == ("customer", 0, 0)


def test_demo_accounts_get_passwords_when_missing():
    with session_scope() as s:
        for cid in ("CUS-1001", "CUS-1002"):
            s.get(Customer, cid).password_hash = None
    assert ensure_demo_passwords() == 2
    assert ensure_demo_passwords() == 0


# ---------- registration ----------

def register(client, name="Ayesha Malik", email="ayesha@gmail.com", password="my-strong-pass"):
    return client.post("/api/v1/auth/register", json={"name": name, "email": email, "password": password})


def test_register_creates_signed_in_free_customer(anon):
    res = register(anon, name="  Ayesha   Malik ", email=" Ayesha@Gmail.COM ")
    assert res.status_code == 201
    assert res.json()["customer"] == {"id": "CUS-1003", "name": "Ayesha Malik", "email": "ayesha@gmail.com",
                                      "plan": "Free", "orders": []}
    assert "acme_session=" in res.headers["set-cookie"]
    assert anon.get("/api/v1/auth/me").json()["id"] == "CUS-1003"


def test_password_is_stored_only_as_a_hash(anon):
    register(anon)
    with session_scope() as s:
        stored = s.get(Customer, "CUS-1003").password_hash
    assert stored.startswith("$argon2") and "my-strong-pass" not in stored


def test_registered_customer_can_sign_in_later(anon):
    register(anon)
    anon.post("/api/v1/auth/logout")
    assert login(anon, email="ayesha@gmail.com", password="my-strong-pass").status_code == 200
    assert login(anon, email="ayesha@gmail.com", password="wrong-password").status_code == 401


def test_customer_ids_go_up(anon):
    register(anon)
    second = register(anon, name="Bilal", email="bilal@gmail.com")
    assert second.json()["customer"]["id"] == "CUS-1004"


@pytest.mark.parametrize("email", ["ayesha@gmail.com", "AYESHA@gmail.com", "ali@example.com"])
def test_duplicate_email_is_refused(anon, email):
    register(anon)
    res = register(anon, email=email)
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


@pytest.mark.parametrize("name,email,password,message", [
    ("", "a@gmail.com", "long-enough-pw", "Enter your name."),
    ("A", "not-an-email", "long-enough-pw", "Enter an email address"),
    ("A", "a@gmail.com", "short", "at least 8 characters"),
    ("A", "a@gmail.com", "password123", "too easy to guess"),
    ("A", "ayesha123@gmail.com", "ayesha123", "too easy to guess"),
])
def test_registration_rules(anon, name, email, password, message):
    res = register(anon, name=name, email=email, password=password)
    assert res.status_code == 422
    assert message in res.json()["detail"]


def test_signups_per_network_are_limited(anon, monkeypatch):
    monkeypatch.setenv("REGISTRATIONS_PER_HOUR", "2")
    config.get_settings.cache_clear()
    with TestClient(app) as client:              # limiter is created at startup with the new limit
        register(client, name="x", email="x@gmail.com", password="short")   # typo: doesn't count
        assert register(client, email="one@gmail.com").status_code == 201
        assert register(client, email="two@gmail.com").status_code == 201
        third = register(client, email="three@gmail.com")
    assert third.status_code == 429
    assert "Too many new accounts" in third.json()["detail"] and int(third.headers["retry-after"]) > 0


def test_registration_can_be_turned_off(anon, monkeypatch):
    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    config.get_settings.cache_clear()
    assert register(anon).status_code == 403
    assert anon.get("/api/v1/auth/options").json() == {"registration_enabled": False}


def test_new_customer_only_sees_their_own_data(anon, fake_llm):
    from langchain_core.messages import AIMessage, ToolMessage
    from tests.fakes import tool_call
    register(anon)
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD-1034"}),     # Ali's order
        AIMessage(content="I couldn't find that order on your account."),
    ]
    data = anon.post("/api/v1/chat", json={"message": "billing: payments for ORD-1034"}).json()
    state = app.state.graph.get_state({"configurable": {"thread_id": f"CUS-1003:{data['session_id']}"}}).values
    result = next(m for m in state["messages"] if isinstance(m, ToolMessage)).content
    assert "not found on this account" in result
