from app.database.connection import session_scope
from app.database.models import SupportTicket
from app.tools.db_tools import create_support_ticket, get_customer_details, get_order_payments


def test_get_customer_details():
    res = get_customer_details.invoke({"customer_id": "CUS-1001"})
    assert res["customer_id"] == "CUS-1001"
    assert res["name"] == "Ali Khan"
    assert [o["order_id"] for o in res["orders"]] == ["ORD-1034"]


def test_get_order_payments_duplicate():
    res = get_order_payments.invoke({"order_id": "ORD-1034", "customer_id": "CUS-1001"})
    assert res["payment_records_count"] == 2
    assert {p["status"] for p in res["payments"]} == {"Successful", "Duplicate"}


def test_order_lookup_is_normalised():
    res = get_order_payments.invoke({"order_id": " ord-1034 ", "customer_id": "CUS-1001"})
    assert res["order_id"] == "ORD-1034"


def test_cannot_read_another_customers_order():
    res = get_order_payments.invoke({"order_id": "ORD-1035", "customer_id": "CUS-1001"})
    assert "error" in res
    assert "payments" not in res and "2500" not in str(res)


def test_create_ticket_persists():
    res = create_support_ticket.invoke({"customer_id": "CUS-1002", "category": "Technical",
                                        "description": "API returns 500", "priority": "Urgent"})
    assert res["status"] == "success"
    assert len(res["ticket_id"]) == len("TCK-") + 8
    with session_scope() as s:
        ticket = s.get(SupportTicket, res["ticket_id"])
        assert (ticket.customer_id, ticket.priority, ticket.status) == ("CUS-1002", "Urgent", "Open")


def test_model_never_sees_customer_id():
    for t in (get_customer_details, get_order_payments, create_support_ticket):
        assert "customer_id" not in t.tool_call_schema.model_json_schema().get("properties", {})
