from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


def test_create_order_and_wallet():
    # create an order in sandbox mode (no provider key)
    payload = {
        "buyer_id": "user_buyer_1",
        "seller_id": "user_seller_1",
        "item_id": "item_123",
        "amount": 10000.0,
        "currency": "KRW",
    }
    r = client.post("/payments/create", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert "order_id" in body

    # check wallet (should be zero until webhook called)
    w = client.get(f"/payments/wallet/{payload['seller_id']}")
    assert w.status_code == 200
    assert w.json().get("balance") == 0.0
