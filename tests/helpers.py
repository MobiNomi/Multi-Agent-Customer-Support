"""Shared test helpers."""

DEMO_PASSWORD = "demo1234"
ALI = "ali@example.com"
SARA = "sara@example.com"


def sign_in(client, email=ALI, password=DEMO_PASSWORD):
    """Sign in through the real endpoint; the TestClient keeps the session cookie afterwards."""
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


def switch_to(client, email):
    client.post("/api/v1/auth/logout")
    return sign_in(client, email)
