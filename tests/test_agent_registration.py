from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_agent_register_returns_ws_token():
    register_payload = {
        "agent_id": "agent-123",
        "secret": "supersecret-agent-token",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test"},
    }
    response = client.post("/register", json=register_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["agent_id"] == "agent-123"
    assert "ws_token" in data
    assert "expires_at" in data
