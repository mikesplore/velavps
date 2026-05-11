import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
