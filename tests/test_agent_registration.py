import pytest
from fastapi.testclient import TestClient

from main import app
from app.services import vela_state as state

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_db():
    """Clean up the database before each test."""
    if state.db:
        conn = state.db._get_connection()
        conn.execute("DELETE FROM agent_credentials")
        conn.execute("DELETE FROM app_agent_links")
        conn.execute("DELETE FROM agent_pairing_sessions")
        conn.execute("DELETE FROM ws_tokens")
        conn.execute("DELETE FROM agents")
        conn.execute("DELETE FROM secrets")
        conn.execute("DELETE FROM audit_events")

    yield

    if state.db:
        conn = state.db._get_connection()
        conn.execute("DELETE FROM agent_credentials")
        conn.execute("DELETE FROM app_agent_links")
        conn.execute("DELETE FROM agent_pairing_sessions")
        conn.execute("DELETE FROM ws_tokens")
        conn.execute("DELETE FROM agents")
        conn.execute("DELETE FROM secrets")
        conn.execute("DELETE FROM audit_events")


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


def test_pairing_happy_path_start_pair_activate():
    start = client.post(
        "/agents/register/start",
        json={
            "agent_name": "android-test-agent",
            "device_info": {"device_fingerprint": "abc123"},
            "tenant_hint": "tenant-a",
        },
    )
    assert start.status_code == 200
    start_data = start.json()
    agent_id = start_data["agent_id"]
    pairing_code = start_data["pairing_code"]
    assert start_data["api_version"]
    assert start_data["pairing_expires_in"] > 0

    pre_pair = client.get(f"/agents/register/status?agent_id={agent_id}")
    assert pre_pair.status_code == 200
    assert pre_pair.json()["status"] == "AWAITING_PAIR"

    pair = client.post(
        "/pair/complete",
        json={"pairing_code": pairing_code, "agent_label": "Pixel 8"},
        headers={"X-Secret": "user-secret-1"},
    )
    assert pair.status_code == 200
    assert pair.json()["status"] == "paired"

    post_pair = client.get(f"/agents/register/status?agent_id={agent_id}")
    assert post_pair.status_code == 200
    post_pair_data = post_pair.json()
    assert post_pair_data["status"] == "PAIRED"
    assert post_pair_data["activation_token"]

    activate = client.post(
        "/agents/register/activate",
        json={
            "agent_id": agent_id,
            "activation_token": post_pair_data["activation_token"],
        },
    )
    assert activate.status_code == 200
    activate_data = activate.json()
    assert activate_data["credential"]
    assert "agent:relay" in activate_data["scopes"]


def test_pairing_code_single_use_and_invalid_code_response():
    start = client.post(
        "/agents/register/start",
        json={"agent_name": "agent-single-use", "device_info": {"device_fingerprint": "xyz"}},
    )
    pairing_code = start.json()["pairing_code"]

    ok = client.post(
        "/pair/complete",
        json={"pairing_code": pairing_code},
        headers={"X-Secret": "user-secret-2"},
    )
    assert ok.status_code == 200

    reused_by_other_user = client.post(
        "/pair/complete",
        json={"pairing_code": pairing_code},
        headers={"X-Secret": "user-secret-3"},
    )
    assert reused_by_other_user.status_code == 400
    assert reused_by_other_user.json()["message"] == "invalid_or_expired_code"


def test_activation_token_is_one_time():
    start = client.post(
        "/agents/register/start",
        json={"agent_name": "agent-activate-once", "device_info": {"device_fingerprint": "finger-activate"}},
    )
    agent_id = start.json()["agent_id"]
    pairing_code = start.json()["pairing_code"]

    pair = client.post(
        "/pair/complete",
        json={"pairing_code": pairing_code},
        headers={"X-Secret": "user-secret-4"},
    )
    assert pair.status_code == 200

    status_response = client.get(f"/agents/register/status?agent_id={agent_id}")
    activation_token = status_response.json()["activation_token"]

    first = client.post(
        "/agents/register/activate",
        json={"agent_id": agent_id, "activation_token": activation_token},
    )
    assert first.status_code == 200

    second = client.post(
        "/agents/register/activate",
        json={"agent_id": agent_id, "activation_token": activation_token},
    )
    assert second.status_code == 400
    assert second.json()["message"] == "invalid_activation_token"
