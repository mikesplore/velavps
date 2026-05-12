import asyncio
import base64
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from main import app


class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/echo":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"echo-response")
        elif self.path == "/binary":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(b"\x01\x02\x03\xff")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_local_server():
    server = HTTPServer(("127.0.0.1", 0), EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


client = TestClient(app)


def test_forwarder_prefers_direct_http_when_available(monkeypatch):
    from services.agent_registry import AgentConnection
    from services.forwarder import Forwarder
    from services.settings import Settings, VPSSettings

    settings = Settings(vps=VPSSettings(api_keys=["supersecret-client-key"], agent_shared_secret="supersecret-agent-token", allow_direct_agent_forwarding=True))
    registry = AsyncMock()
    agent = AgentConnection(agent_id="prefers-http", public_address="http://127.0.0.1:1234")
    agent.websocket = object()
    registry.get_agent.return_value = agent

    forwarder = Forwarder(settings=settings, registry=registry)
    forwarder._forward_via_http = AsyncMock(return_value={"status_code": 200, "headers": {}, "body": b"ok"})
    forwarder._forward_via_websocket = AsyncMock(return_value={"status_code": 500, "headers": {}, "body": b"bad"})

    result = asyncio.run(forwarder.forward("prefers-http", "GET", "/test", {}, {}, None))

    assert result["status_code"] == 200
    forwarder._forward_via_http.assert_awaited_once()
    forwarder._forward_via_websocket.assert_not_awaited()


def test_forward_to_registered_agent_direct_http():
    server = start_local_server()
    try:
        port = server.server_port
        public_address = f"http://127.0.0.1:{port}"
        register_payload = {
            "agent_id": "forward-agent",
            "secret": "supersecret-agent-token",
            "public_address": public_address,
            "metadata": {"test": "forward"},
        }
        response = client.post("/register", json=register_payload)
        assert response.status_code == 200

        response = client.get(
            "/relay/forward-agent/echo",
            headers={"X-API-Key": "supersecret-client-key"},
        )

        assert response.status_code == 200
        assert response.text == "echo-response"
    finally:
        server.shutdown()
        server.server_close()


def test_forward_preserves_binary_response_direct_http():
    server = start_local_server()
    try:
        port = server.server_port
        public_address = f"http://127.0.0.1:{port}"
        register_payload = {
            "agent_id": "binary-agent",
            "secret": "supersecret-agent-token",
            "public_address": public_address,
            "metadata": {"test": "binary"},
        }
        response = client.post("/register", json=register_payload)
        assert response.status_code == 200

        response = client.get(
            "/relay/binary-agent/binary",
            headers={"X-API-Key": "supersecret-client-key"},
        )

        assert response.status_code == 200
        assert response.content == b"\x01\x02\x03\xff"
        assert response.headers["content-type"] == "application/octet-stream"
    finally:
        server.shutdown()
        server.server_close()


def test_encode_body_for_websocket_preserves_json_text():
    from services.forwarder import Forwarder
    from services.settings import Settings, VPSSettings
    from services.agent_registry import AgentRegistry

    settings = Settings(vps=VPSSettings(api_keys=["supersecret-client-key"], agent_shared_secret="supersecret-agent-token", allow_direct_agent_forwarding=False))
    forwarder = Forwarder(settings=settings, registry=AgentRegistry())

    payload = b'{"title":"Alert","message":"Screenshot saved successfully"}'
    result = forwarder._encode_body_for_websocket(payload)

    assert result == {"body": payload.decode("utf-8"), "body_encoding": "utf-8"}


def test_encode_body_for_websocket_base64_encodes_binary():
    from services.forwarder import Forwarder
    from services.settings import Settings, VPSSettings
    from services.agent_registry import AgentRegistry

    settings = Settings(vps=VPSSettings(api_keys=["supersecret-client-key"], agent_shared_secret="supersecret-agent-token", allow_direct_agent_forwarding=False))
    forwarder = Forwarder(settings=settings, registry=AgentRegistry())

    payload = b"\x00\x01\x02\xff"
    result = forwarder._encode_body_for_websocket(payload)

    assert result == {
        "body": base64.b64encode(payload).decode("ascii"),
        "body_encoding": "base64",
    }
