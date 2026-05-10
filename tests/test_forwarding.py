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
            "public_address": public_address,
            "metadata": {"test": "forward"},
        }
        response = client.post(
            "/agents/register",
            json=register_payload,
            headers={"X-Agent-Token": "supersecret-agent-token"},
        )
        assert response.status_code == 200

        forward_payload = {
            "method": "GET",
            "path": "/echo",
            "headers": {"Accept": "text/plain"},
            "query_params": {},
            "body": None,
        }
        response = client.post(
            "/agents/forward-agent/forward",
            json=forward_payload,
            headers={"X-API-Key": "supersecret-client-key"},
        )

        assert response.status_code == 200
        assert response.text == "echo-response"
    finally:
        server.shutdown()
        server.server_close()
