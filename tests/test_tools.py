import pytest
from app.tools.db_tools import get_customer_details, get_order_payments

def test_get_customer_details():
    res = get_customer_details.invoke({"customer_id": "CUS-1001"})
    assert res["customer_id"] == "CUS-1001"
    assert "name" in res
    assert len(res["orders"]) > 0

def test_get_order_payments_duplicate():
    res = get_order_payments.invoke({"order_id": "ORD-1034"})
    assert res["order_id"] == "ORD-1034"
    assert res["payment_records_count"] == 2