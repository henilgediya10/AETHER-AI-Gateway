from fastapi.testclient import TestClient
from app.main import app

def test_security_headers_and_request_correlation():
    response = TestClient(app).get("/health/live")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]

def test_unauthenticated_chat_is_rejected():
    response = TestClient(app).post("/chat", json={"message": "hello"})
    assert response.status_code == 401
