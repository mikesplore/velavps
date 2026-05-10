from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_agent_register_and_list():
    register_payload = {
        "agent_id": "agent-123",
        "public_address": "http://127.0.0.1:5001",
        "metadata": {"location": "test"},
    }
    response = client.post(
        "/agents/register",
        json=register_payload,
        headers={"X-Agent-Token": "supersecret-agent-token"},
    )
    assert response.status_code == 200
    assert response.json()["agent"]["agent_id"] == "agent-123"

    response = client.get("/agents", headers={"X-API-Key": "supersecret-client-key"})
    assert response.status_code == 200
    assert any(agent["agent_id"] == "agent-123" for agent in response.json()["agents"])
