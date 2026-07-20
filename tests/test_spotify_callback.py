"""
Tests for Spotify OAuth callback endpoint.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a test client with mocked state."""
    import sys
    sys.path.insert(0, "/home/mike/PycharmProjects/velavps")

    from app.services.vela_agent_registry import AgentRegistry
    from app.services.vela_database import VelaDatabase
    from app.services.vela_forwarder import Forwarder
    from app.services.vela_settings import Settings, VPSSettings
    from app.services import vela_state as state

    # Setup mock state
    import app.services.vela_state as state_module

    settings = Settings(vps=VPSSettings())
    db = VelaDatabase(":memory:")
    registry = AgentRegistry()

    state_module.settings = settings
    state_module.db = db
    state_module.forwarder = Forwarder(settings, registry, db)
    state_module.registry = registry

    from main import app
    return TestClient(app)


def test_callback_endpoint_is_public(client):
    """
    Test that /relay/{agent_id}/callback is accessible without auth.
    This is critical for Spotify OAuth callbacks which cannot carry relay auth headers.
    """
    import threading
    import http.server
    import json

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_port
    public_address = f"http://127.0.0.1:{port}"

    register_payload = {
        "agent_id": "mike",
        "secret": "test-secret-123",
        "public_address": public_address,
        "metadata": {},
    }
    client.post("/register", json=register_payload)

    response = client.get(
        "/relay/mike/callback",
        params={"code": "spotify-auth-code-xyz", "state": "random-state-123"},
    )

    assert response.status_code == 200

    server.shutdown()


def test_relay_endpoint_still_requires_auth(client):
    """
    Verify that normal relay endpoints still require auth after adding callback exception.
    """
    response = client.get("/relay/mike/some-path")
    assert response.status_code == 401


def test_callback_validates_agent_exists(client):
    """
    Callback should return 404 if agent doesn't exist.
    """
    response = client.get(
        "/relay/nonexistent/callback",
        params={"code": "some-code"},
    )
    assert response.status_code == 404


def test_callback_forwards_query_params(client):
    """
    Verify that callback forwards all query params (including code) to agent.
    """
    captured_requests = []

    import threading
    import http.server
    import json

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            captured_requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"received": True}).encode())

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_port
    public_address = f"http://127.0.0.1:{port}"

    register_payload = {
        "agent_id": "mike",
        "secret": "test-secret-123",
        "public_address": public_address,
        "metadata": {},
    }
    client.post("/register", json=register_payload)

    response = client.get(
        "/relay/mike/callback",
        params={"code": "abc123", "state": "xyz789"},
    )

    assert response.status_code == 200
    assert len(captured_requests) == 1
    assert captured_requests[0].startswith("/spotify/callback?")
    assert "code=abc123" in captured_requests[0]
    assert "state=xyz789" in captured_requests[0]

    server.shutdown()