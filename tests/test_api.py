import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "multi-agent-support-api"}

def test_chat_billing_flow():
    payload = {
        "message": "I was charged twice for order ORD-1034",
        "customer_id": "CUS-1001"
    }
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["assigned_agent"] == "billing_agent"
    assert data["intent"] == "billing"
    assert "ORD-1034" in data["response"] or "refund" in data["response"].lower()