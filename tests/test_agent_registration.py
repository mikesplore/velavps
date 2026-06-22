from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_agent_register_auto_generates_secret():
    """Test that registering without any auth auto-generates a secret."""
    register_payload = {
        "agent_id": "agent-123",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test"},
    }
    response = client.post("/register", json=register_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["agent_id"] == "agent-123"
    assert "secret" in data
    assert len(data["secret"]) > 0  # A secret was auto-generated
    assert "ws_token" in data
    assert "expires_at" in data


def test_agent_reregister_returns_same_secret():
    """Test that re-registering the same agent_id returns the same secret."""
    register_payload = {
        "agent_id": "agent-456",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test"},
    }

    # First registration
    resp1 = client.post("/register", json=register_payload)
    assert resp1.status_code == 200
    secret1 = resp1.json()["secret"]

    # Second registration
    resp2 = client.post("/register", json=register_payload)
    assert resp2.status_code == 200
    secret2 = resp2.json()["secret"]

    # Same secret returned for the same agent_id
    assert secret1 == secret2


def test_agent_regenerate_secret():
    """Test that re-registering with regenerate_secret=true issues a new secret."""
    register_payload = {
        "agent_id": "agent-regenerate",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test"},
    }

    # First registration
    resp1 = client.post("/register", json=register_payload)
    assert resp1.status_code == 200
    secret1 = resp1.json()["secret"]

    # Re-register with regenerate_secret=true
    resp2 = client.post("/register", json={**register_payload, "regenerate_secret": True})
    assert resp2.status_code == 200
    secret2 = resp2.json()["secret"]

    # A new secret was issued
    assert secret1 != secret2