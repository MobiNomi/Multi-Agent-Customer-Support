"""Actions with confirmation: the assistant proposes, the customer confirms, staff approve refunds."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.database.connection import session_scope
from app.database.models import Customer, Order, Payment, PendingAction, SupportTicket, utcnow_naive
from app.graph.agents import AGENT_TOOLS, COMMON_RULES, PROMPTS
from app.guardrails.nodes import output_guard
from app.main import app
from app.tools.action_tools import cancel_order, get_my_requests, request_refund, update_email
from tests.fakes import tool_call
from tests.helpers import ALI, DEMO_PASSWORD, SARA, sign_in, switch_to

STAFF = "staff@acmecloud.example"


@pytest.fixture
def anon():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(anon):
    sign_in(anon)            # Ali, CUS-1001
    return anon


def staff_sign_in(client, email=STAFF, password=DEMO_PASSWORD):
    res = client.post("/api/v1/staff/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


def payment_status(txn):
    with session_scope() as s:
        return s.query(Payment).filter_by(transaction_id=txn).one().status


def propose_refund_in_chat(client, fake_llm, txn="TXN-8922", session_id=None):
    fake_llm.agent_replies = [
        tool_call("get_order_payments", {"order_id": "ORD-1034"}),
        tool_call("request_refund", {"transaction_id": txn, "reason": "Charged twice"}, call_id="call_2"),
        AIMessage(content=f"{txn} is a duplicate. I've prepared a refund request: press Confirm below to send "
                          "it to our billing team."),
    ]
    body = {"message": "I was charged twice for order ORD-1034, please refund it"}
    if session_id:
        body["session_id"] = session_id
    res = client.post("/api/v1/chat", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ---------- the tools only propose ----------

def test_request_refund_creates_a_proposal_and_changes_nothing():
    result = request_refund.invoke({"transaction_id": "txn-8922", "reason": "duplicate",
                                    "customer_id": "CUS-1001", "thread_id": "CUS-1001:s1"})
    assert result["status"] == "needs_confirmation" and result["action_id"].startswith("ACT-")
    assert "Refund $149.00 for duplicate payment TXN-8922 on order ORD-1034" == result["summary"]
    assert "Nothing has been refunded yet" in result["instructions"]
    assert payment_status("TXN-8922") == "Duplicate"
    with session_scope() as s:
        a = s.get(PendingAction, result["action_id"])
        assert (a.status, a.requires_staff, a.thread_id, a.amount) == ("awaiting_customer", True, "CUS-1001:s1", 149.0)


def test_asking_twice_shows_the_same_request():
    args = {"transaction_id": "TXN-8922", "reason": "dup", "customer_id": "CUS-1001", "thread_id": None}
    assert request_refund.invoke(args)["action_id"] == request_refund.invoke(args)["action_id"]


def test_refund_for_someone_elses_payment_is_refused():
    result = request_refund.invoke({"transaction_id": "TXN-9001", "reason": "x", "customer_id": "CUS-1001"})
    assert "not found on this account" in result["error"]


def test_refund_for_refunded_payment_is_refused():
    with session_scope() as s:
        s.query(Payment).filter_by(transaction_id="TXN-8922").one().status = "Refunded"
    result = request_refund.invoke({"transaction_id": "TXN-8922", "reason": "x", "customer_id": "CUS-1001"})
    assert "already been refunded" in result["error"]


@pytest.mark.parametrize("email, message", [
    ("not-an-email", "isn't a valid email"),
    ("ALI@example.com", "already the email"),
    ("sara@example.com", "already used by another account"),
])
def test_update_email_checks_the_address(email, message):
    result = update_email.invoke({"new_email": email, "customer_id": "CUS-1001"})
    assert message in result["error"]


def test_only_processing_orders_can_be_cancelled():
    delivered = cancel_order.invoke({"order_id": "ORD-1034", "reason": "x", "customer_id": "CUS-1001"})
    assert "is Delivered, so it can't be cancelled" in delivered["error"]
    sara = cancel_order.invoke({"order_id": "ord-1035", "reason": "changed my mind", "customer_id": "CUS-1002"})
    assert sara["status"] == "needs_confirmation"
    assert sara["summary"] == "Cancel order ORD-1035 ($2,500.00) and request a refund of $2,500.00"
    assert "refund then goes to the billing team" in sara["instructions"]


def test_tools_hide_injected_arguments_from_the_model():
    for t in (request_refund, update_email, cancel_order, get_my_requests):
        fields = t.tool_call_schema.model_json_schema()["properties"]
        assert "customer_id" not in fields and "thread_id" not in fields


def test_agents_have_the_action_tools():
    names = {a: {t.name for t in tools} for a, tools in AGENT_TOOLS.items()}
    assert {"request_refund", "cancel_order", "get_my_requests"} <= names["billing_agent"]
    assert "update_email" in names["general_agent"] and "request_refund" not in names["general_agent"]


# ---------- the whole refund story through the API ----------

def test_refund_proposed_confirmed_approved_and_seen_by_customer(client, fake_llm):
    data = propose_refund_in_chat(client, fake_llm)
    assert len(data["actions"]) == 1
    action = data["actions"][0]
    assert action["status"] == "awaiting_customer" and action["kind"] == "refund" and action["amount"] == 149.0
    assert "press Confirm" in data["response"]
    sid = data["session_id"]

    # Customer confirms: it goes to staff, nothing is refunded yet.
    res = client.post(f"/api/v1/actions/{action['id']}/confirm")
    assert res.status_code == 200
    body = res.json()
    assert body["action"]["status"] == "awaiting_staff"
    assert body["updates"][0]["kind"] == "action_submitted" and body["updates"][0]["author"] is None
    assert payment_status("TXN-8922") == "Duplicate"
    assert client.post(f"/api/v1/actions/{action['id']}/confirm").status_code == 409   # double click

    # A customer can't approve their own refund.
    assert client.post(f"/api/v1/staff/actions/{action['id']}/approve", json={}).status_code == 401

    # Staff sign in on the same browser (separate cookie) and see it in the queue.
    staff_sign_in(client)
    queue = client.get("/api/v1/staff/queue").json()
    assert [a["id"] for a in queue["actions"]] == [action["id"]]
    assert queue["actions"][0]["customer_name"] == "Ali Khan"
    detail = client.get(f"/api/v1/staff/actions/{action['id']}").json()
    assert detail["payment"]["status"] == "Duplicate" and detail["customer"]["email"] == ALI
    assert detail["order"]["order_id"] == "ORD-1034" and len(detail["order"]["payments"]) == 2
    assert detail["conversation"][0] == {"role": "customer", "content": "I was charged twice for order ORD-1034, "
                                                                        "please refund it",
                                         "author": None, "kind": None, "created_at": None}
    assert detail["conversation"][-1]["kind"] == "action_submitted"

    res = client.post(f"/api/v1/staff/actions/{action['id']}/approve", json={"note": "Sorry about that"})
    assert res.status_code == 200
    decision = res.json()
    assert decision["action"]["status"] == "completed" and decision["delivered_to_chat"] is True
    assert payment_status("TXN-8922") == "Refunded"
    assert client.post(f"/api/v1/staff/actions/{action['id']}/approve", json={}).status_code == 409
    assert client.get("/api/v1/staff/queue").json()["recent"][0]["resolved_by_name"] == "Hina Raza"

    # The customer's chat page polls and sees the staff message, and the card's new status.
    polled = client.get(f"/api/v1/sessions/{sid}/updates", params={"after": body["updates"][0]["id"]}).json()
    assert [u["kind"] for u in polled["updates"]] == ["staff_approved"]
    approved = polled["updates"][0]
    assert approved["author"] == "Hina Raza" and "has been approved" in approved["content"]
    assert "Note from Hina: Sorry about that." in approved["content"]
    assert polled["actions"][0]["status"] == "completed"

    # After a reload, the history has the support messages and the card under the right reply.
    history = client.get(f"/api/v1/sessions/{sid}/messages").json()
    roles = [m["role"] for m in history["messages"]]
    assert roles == ["customer", "assistant", "support", "support"]
    assert history["messages"][1]["actions"][0]["status"] == "completed"
    assert history["last_update_id"] == approved["id"]

    # The next turn: the assistant is told about the approval before it answers.
    fake_llm.agent_replies = [AIMessage(content="Yes, your refund for TXN-8922 has been approved.")]
    nxt = client.post("/api/v1/chat", json={"message": "did my refund go through?", "session_id": sid}).json()
    seen = " ".join(str(m.content) for m in fake_llm.calls[-1])
    assert "[Update from Hina Raza of the AcmeCloud support team]" in seen and "has been approved" in seen
    # ...and because staff really approved one, saying so is not rewritten.
    assert nxt["response"] == "Yes, your refund for TXN-8922 has been approved."
    history = client.get(f"/api/v1/sessions/{sid}/messages").json()["messages"]
    assert [m["role"] for m in history] == ["customer", "assistant", "support", "support", "customer", "assistant"]


def test_staff_rejection_needs_a_reason_and_reaches_the_customer(client, fake_llm):
    data = propose_refund_in_chat(client, fake_llm)
    action_id = data["actions"][0]["id"]
    client.post(f"/api/v1/actions/{action_id}/confirm")
    staff_sign_in(client)
    assert client.post(f"/api/v1/staff/actions/{action_id}/reject", json={"reason": " "}).status_code == 422
    res = client.post(f"/api/v1/staff/actions/{action_id}/reject",
                      json={"reason": "This charge was a pre-authorisation and has already dropped off"})
    assert res.status_code == 200 and res.json()["action"]["status"] == "rejected"
    assert payment_status("TXN-8922") == "Duplicate"
    updates = client.get(f"/api/v1/sessions/{data['session_id']}/updates").json()["updates"]
    assert updates[-1]["kind"] == "staff_rejected"
    assert "Reason: This charge was a pre-authorisation and has already dropped off." in updates[-1]["content"]


def test_customer_can_cancel_or_withdraw(client, fake_llm):
    data = propose_refund_in_chat(client, fake_llm)
    action_id = data["actions"][0]["id"]
    res = client.post(f"/api/v1/actions/{action_id}/cancel")
    assert res.json()["action"]["status"] == "cancelled"
    assert res.json()["updates"][0]["kind"] == "action_cancelled"
    assert client.post(f"/api/v1/actions/{action_id}/confirm").status_code == 409

    # A confirmed refund can still be withdrawn before staff decide; then staff can't approve it.
    second = propose_refund_in_chat(client, fake_llm, session_id=data["session_id"])["actions"][0]["id"]
    assert second != action_id
    client.post(f"/api/v1/actions/{second}/confirm")
    assert client.post(f"/api/v1/actions/{second}/cancel").json()["action"]["status"] == "cancelled"
    staff_sign_in(client)
    assert client.post(f"/api/v1/staff/actions/{second}/approve", json={}).status_code == 409


def test_unconfirmed_requests_expire(client, fake_llm):
    action_id = propose_refund_in_chat(client, fake_llm)["actions"][0]["id"]
    with session_scope() as s:
        s.get(PendingAction, action_id).expires_at = utcnow_naive() - timedelta(minutes=1)
    res = client.post(f"/api/v1/actions/{action_id}/confirm")
    assert res.status_code == 410 and "expired" in res.json()["detail"]
    assert client.get(f"/api/v1/actions/{action_id}").json()["status"] == "expired"


def test_requests_are_private_to_their_customer(client, fake_llm):
    action_id = propose_refund_in_chat(client, fake_llm)["actions"][0]["id"]
    switch_to(client, SARA)
    assert client.get(f"/api/v1/actions/{action_id}").status_code == 404
    assert client.post(f"/api/v1/actions/{action_id}/confirm").status_code == 404
    assert client.post(f"/api/v1/actions/{action_id}/cancel").status_code == 404


def test_email_change_happens_on_confirm(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("update_email", {"new_email": "Ali.Khan@Example.org"}),
        AIMessage(content="Your email has been updated to ali.khan@example.org."),   # a false claim
    ]
    data = client.post("/api/v1/chat", json={"message": "please change my email to Ali.Khan@Example.org"}).json()
    assert data["assigned_agent"] == "general_agent"
    assert "has been updated" not in data["response"] and "press Confirm below" in data["response"]
    assert "promise_rewritten" in [g["kind"] for g in data["guardrails"]]
    action = data["actions"][0]
    assert action["summary"] == "Change your sign-in email from ali@example.com to ali.khan@example.org"

    res = client.post(f"/api/v1/actions/{action['id']}/confirm").json()
    assert res["action"]["status"] == "completed"
    assert res["updates"][0]["kind"] == "action_completed" and "ali.khan@example.org" in res["updates"][0]["content"]
    assert client.get("/api/v1/auth/me").json()["email"] == "ali.khan@example.org"   # still signed in

    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"email": ALI, "password": DEMO_PASSWORD}).status_code == 401
    sign_in(client, "ali.khan@example.org")


def test_email_change_fails_cleanly_if_the_address_was_taken_meanwhile(client, fake_llm):
    fake_llm.agent_replies = [tool_call("update_email", {"new_email": "new@example.com"}),
                              AIMessage(content="Press Confirm to change it.")]
    action_id = client.post("/api/v1/chat", json={"message": "change my email to new@example.com"}).json()["actions"][0]["id"]
    with session_scope() as s:
        s.get(Customer, "CUS-1002").email = "new@example.com"
    res = client.post(f"/api/v1/actions/{action_id}/confirm").json()
    assert res["action"]["status"] == "failed" and res["updates"][0]["kind"] == "action_failed"
    assert client.get("/api/v1/auth/me").json()["email"] == ALI


def test_order_cancellation_sends_the_refund_to_staff(client, fake_llm):
    switch_to(client, SARA)
    fake_llm.agent_replies = [
        tool_call("cancel_order", {"order_id": "ORD-1035", "reason": "No longer needed"}),
        AIMessage(content="I've prepared the cancellation of ORD-1035. Press Confirm below to cancel it."),
    ]
    data = client.post("/api/v1/chat", json={"message": "please cancel my order ORD-1035, I don't need it"}).json()
    action_id = data["actions"][0]["id"]
    res = client.post(f"/api/v1/actions/{action_id}/confirm").json()
    assert res["action"]["status"] == "completed"
    assert "order ORD-1035 is cancelled" in res["updates"][0]["content"]
    assert "$2,500.00 for payment TXN-9001 is now with our billing team" in res["updates"][0]["content"]
    with session_scope() as s:
        assert s.get(Order, "ORD-1035").status == "Cancelled"
        refund = s.query(PendingAction).filter_by(kind="refund", subject="TXN-9001").one()
        assert (refund.status, refund.parent_id, refund.thread_id) == ("awaiting_staff", action_id,
                                                                       f"CUS-1002:{data['session_id']}")
        refund_id = refund.id

    staff_sign_in(client)
    detail = client.get(f"/api/v1/staff/actions/{refund_id}").json()
    assert detail["parent_summary"].startswith("Cancel order ORD-1035")
    client.post(f"/api/v1/staff/actions/{refund_id}/approve", json={})
    assert payment_status("TXN-9001") == "Refunded"


def test_get_my_requests_reports_status(client, fake_llm):
    action_id = propose_refund_in_chat(client, fake_llm)["actions"][0]["id"]
    client.post(f"/api/v1/actions/{action_id}/confirm")
    result = get_my_requests.invoke({"customer_id": "CUS-1001"})
    assert result["requests"][0]["status"] == "With the billing team for approval"
    assert get_my_requests.invoke({"customer_id": "CUS-1002"})["requests"] == []


def test_refund_already_waiting_for_staff_is_not_requested_again(client, fake_llm):
    data = propose_refund_in_chat(client, fake_llm)
    client.post(f"/api/v1/actions/{data['actions'][0]['id']}/confirm")
    again = request_refund.invoke({"transaction_id": "TXN-8922", "reason": "x", "customer_id": "CUS-1001"})
    assert again["status"] == "already_requested" and again["action_id"] == data["actions"][0]["id"]


# ---------- the output guard and the assistant's instructions ----------

@pytest.mark.parametrize("claim", [
    "Your refund request has been submitted.", "Your order has been cancelled.",
    "I've updated your email.", "Your email address has been changed to a@b.co.",
])
def test_done_claims_right_after_a_proposal_are_rewritten(claim):
    reply, kinds = output_guard().check(f"Here you go. {claim}", [], proposed_action=True)
    assert "Nothing has changed yet" in reply and kinds == ["promise_rewritten"]


def test_normal_proposal_replies_are_not_mistaken_for_prompt_leaks():
    replies = [
        "TXN-8922 is the duplicate charge on ORD-1034. I've prepared a refund request for $149.00. Press Confirm "
        "below to send it to our billing team, who review refund requests within 1 business day.",
        "I can change the email address you sign in with to new@example.com. Press Confirm below and it will "
        "change straight away.",
        "Order ORD-1035 is still Processing, so it can be cancelled. Press Confirm below to cancel it; the refund "
        "then goes to the billing team for approval.",
    ]
    for reply in replies:
        assert not output_guard().leaks_prompt(reply), reply


def test_prompts_mention_every_action_tool():
    text = " ".join(PROMPTS.values()) + COMMON_RULES
    for name in ("request_refund", "cancel_order", "update_email", "get_my_requests"):
        assert name in text


# ---------- tickets in the staff console ----------

def test_ticket_from_chat_gets_staff_reply_in_the_same_chat(client, fake_llm):
    fake_llm.agent_replies = [
        tool_call("create_support_ticket", {"category": "Technical", "description": "Webhooks stopped firing"}),
        AIMessage(content="I've opened a ticket for the engineers."),
    ]
    data = client.post("/api/v1/chat", json={"message": "my webhooks stopped working, api error"}).json()
    ticket_id, sid = data["ticket_id"], data["session_id"]
    with session_scope() as s:
        assert s.get(SupportTicket, ticket_id).thread_id == f"CUS-1001:{sid}"

    staff_sign_in(client)
    queue = client.get("/api/v1/staff/queue").json()
    assert ticket_id in [t["id"] for t in queue["tickets"]]
    detail = client.get(f"/api/v1/staff/tickets/{ticket_id}").json()
    assert detail["has_conversation"] and detail["conversation"][0]["role"] == "customer"

    assert client.post(f"/api/v1/staff/tickets/{ticket_id}/reply", json={"message": " "}).status_code == 422
    res = client.post(f"/api/v1/staff/tickets/{ticket_id}/reply",
                      json={"message": "We found the problem and fixed it. Webhooks are firing again."}).json()
    assert res["delivered_to_chat"] and res["ticket"]["replies"][0]["author"] == "Hina Raza"
    res = client.post(f"/api/v1/staff/tickets/{ticket_id}/close", json={}).json()
    assert res["ticket"]["status"] == "Closed"
    assert client.post(f"/api/v1/staff/tickets/{ticket_id}/reply", json={"message": "hello"}).status_code == 409
    assert ticket_id not in [t["id"] for t in client.get("/api/v1/staff/queue").json()["tickets"]]

    updates = client.get(f"/api/v1/sessions/{sid}/updates").json()["updates"]
    assert [u["kind"] for u in updates] == ["ticket_reply", "ticket_closed"]
    assert updates[0]["content"].startswith("We found the problem")


def test_escalation_ticket_is_linked_to_the_chat(client, fake_llm):
    data = client.post("/api/v1/chat", json={"message": "I want to talk to a person"}).json()
    with session_scope() as s:
        assert s.get(SupportTicket, data["ticket_id"]).thread_id == f"CUS-1001:{data['session_id']}"


def test_reply_on_a_ticket_without_chat_is_saved_but_not_delivered(anon):
    staff_sign_in(anon)
    res = anon.post("/api/v1/staff/tickets/TCK-9001/reply", json={"message": "We're looking into it."}).json()
    assert res["delivered_to_chat"] is False and len(res["ticket"]["replies"]) == 1


# ---------- who can see what ----------

def test_staff_and_customers_are_kept_apart(anon):
    # Staff can't use the customer sign-in, customers can't use the staff sign-in.
    res = anon.post("/api/v1/auth/login", json={"email": STAFF, "password": DEMO_PASSWORD})
    assert res.status_code == 403 and "/staff" in res.json()["detail"]
    res = anon.post("/api/v1/staff/login", json={"email": ALI, "password": DEMO_PASSWORD})
    assert res.status_code == 403
    assert anon.post("/api/v1/staff/login", json={"email": STAFF, "password": "wrong"}).status_code == 401

    # A staff token used as a Bearer token on customer endpoints, and a customer token on staff endpoints.
    staff_token = staff_sign_in(anon)["access_token"]
    customer_token = sign_in(anon)["access_token"]
    assert anon.post("/api/v1/chat", json={"message": "hi"},
                     headers={"Authorization": f"Bearer {staff_token}"}).status_code == 403
    assert anon.get("/api/v1/staff/queue", headers={"Authorization": f"Bearer {customer_token}"}).status_code == 403
    # Both cookies live side by side in one browser.
    assert anon.get("/api/v1/auth/me").json()["email"] == ALI
    assert anon.get("/api/v1/staff/me").json()["email"] == STAFF
    anon.post("/api/v1/staff/logout")
    assert anon.get("/api/v1/staff/queue").status_code == 401
    assert anon.get("/api/v1/auth/me").status_code == 200


def test_staff_console_page_is_served(anon):
    res = anon.get("/staff")
    assert res.status_code == 200 and "Support console" in res.text
    assert anon.get("/static/staff.js").status_code == 200


def test_old_database_gets_ticket_columns_and_demo_staff(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    from app.database.migrations import ensure_schema
    from app.database.seed import ensure_demo_staff
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tickets (id VARCHAR PRIMARY KEY, customer_id VARCHAR, category VARCHAR, "
                          "priority VARCHAR, status VARCHAR, description VARCHAR, created_at DATETIME)"))
    assert set(ensure_schema(engine)) == {"tickets.thread_id", "tickets.closed_at"}
    assert {"thread_id", "closed_at"} <= {c["name"] for c in inspect(engine).get_columns("tickets")}

    with session_scope() as s:   # the test database: remove the staff member, as in a pre-console database
        s.delete(s.get(Customer, "STF-1001"))
    assert ensure_demo_staff() is True and ensure_demo_staff() is False


def test_create_staff_script_makes_a_console_account(anon, capsys, monkeypatch):
    import sys
    from scripts import create_staff
    monkeypatch.setattr(sys, "argv", ["create_staff", "Omar@AcmeCloud.example", "Omar Sheikh", "--generate"])
    assert create_staff.main() == 0
    out = capsys.readouterr().out
    assert "STF-1002" in out
    password = out.strip().splitlines()[-1].split("Password: ")[1]
    staff_sign_in(anon, "omar@acmecloud.example", password)
    monkeypatch.setattr(sys, "argv", ["create_staff", "omar@acmecloud.example", "Omar", "--generate"])
    assert create_staff.main() == 1
