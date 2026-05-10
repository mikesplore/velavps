from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_missing_api_key_rejected():
    response = client.post("/relay/agent-123/test", json={})
    assert response.status_code == 401


def test_invalid_api_key_rejected():
    response = client.post("/relay/agent-123/test", json={}, headers={"X-API-Key": "bad-key"})
    assert response.status_code == 403
