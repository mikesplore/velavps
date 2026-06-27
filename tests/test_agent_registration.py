import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_db():
    """Clean up the database before each test."""
    from app.services import vela_state as state
    
    # Store original db reference
    original_db = state.db
    
    # Create a fresh database for each test
    if state.db:
        state.db._get_connection().execute("DELETE FROM agents")
        state.db._get_connection().execute("DELETE FROM secrets")
    
    yield
    
    # Cleanup after test
    if state.db:
        state.db._get_connection().execute("DELETE FROM agents")
        state.db._get_connection().execute("DELETE FROM secrets")


def test_agent_register_auto_generates_secret():
    """Test that registering auto-generates and returns a secret."""
    register_payload = {
        "agent_id": "agent-register-123",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test"},
    }
    response = client.post("/register", json=register_payload)
    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["agent_id"] == "agent-register-123"
    assert "secret" in data
    assert len(data["secret"]) > 0  # A secret was auto-generated
    assert "ws_token" in data
    assert "expires_at" in data


def test_agent_reregister_fails():
    """Test that re-registering the same agent_id returns 409 Conflict."""
    register_payload = {
        "agent_id": "agent-reregister-456",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test"},
    }

    # First registration
    resp1 = client.post("/register", json=register_payload)
    assert resp1.status_code == 200
    secret1 = resp1.json()["secret"]

    # Second registration should be rejected
    resp2 = client.post("/register", json=register_payload)
    assert resp2.status_code == 409
    response_text = resp2.text.lower()
    assert "already registered" in response_text


def test_unique_agent_ids_required():
    """Test that different devices must use different agent_ids."""
    register_payload1 = {
        "agent_id": "unique-agent-789",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test1"},
    }
    
    register_payload2 = {
        "agent_id": "unique-agent-789",
        "public_address": "http://127.0.0.1:5002",
        "metadata": {"location": "test2"},
    }

    # First registration succeeds
    resp1 = client.post("/register", json=register_payload1)
    assert resp1.status_code == 200
    secret1 = resp1.json()["secret"]

    # Second registration with different device but same agent_id fails
    resp2 = client.post("/register", json=register_payload2)
    assert resp2.status_code == 409
    response_text = resp2.text.lower()
    assert "already registered" in response_text
