import asyncio
import json
import websockets
from fastapi.testclient import TestClient
from main import app
import services.vela_state as state

client = TestClient(app)

async def reproduce_500():
    # 1. Register agent
    register_payload = {
        "agent_id": "my-laptop",
        "secret": "supersecret-agent-token",
    }
    response = client.post("/register", json=register_payload)
    assert response.status_code == 200
    data = response.json()
    ws_token = data["ws_token"]
    
    # 2. Connect via WebSocket
    # We need to use a real websocket connection because TestClient's websocket doesn't easily interop with our state
    # But wait, we can mock the websocket in the registry if we want to test the forwarder.
    
    # Let's try to use the actual app with a websocket server
    # Since we are in a test environment, we might need to run the app in a separate thread/process
    # Or just test the Forwarder directly with a mock websocket.
    
    from unittest.mock import AsyncMock
    mock_ws = AsyncMock()
    
    agent = await state.registry.set_websocket_connection("my-laptop", mock_ws)
    
    # Define what the mock websocket should do when it receives a forward_request
    async def mock_send_json(message):
        if message["type"] == "forward_request":
            # Simulate the agent returning a 500-like response or something that causes the issue
            # The issue report showed "Local auth failed: ..." which looks like a body.
            resp = {
                "type": "forward_response",
                "request_id": message["request_id"],
                "status_code": 500,
                "headers": {"content-type": "text/plain"},
                "body": "Local auth failed: HTTPConnectionPool(host='localhost', port=8765): Max retries exceeded with url: /auth/token (Caused by NewConnectionError(\"HTTPConnection(host='localhost', port=8765): Failed to establish a new connection: [Errno 111] Connection refused\"))",
                "body_encoding": "utf-8"
            }
            # We need to push this response back to the agent's pending_responses
            # In real life, this happens in routers/vela_relay.py agent_tunnel loop.
            async with agent.pending_lock:
                future = agent.pending_responses.get(message["request_id"])
                if future:
                    future.set_result(resp)

    mock_ws.send_json.side_effect = mock_send_json

    # 3. Make a relay request
    response = client.get(
        "/relay/my-laptop/system/cpu",
        headers={"X-API-Key": "Mikesplore2030!!!"}
    )
    
    print(f"Status Code: {response.status_code}")
    print(f"Response Body: {response.text}")

if __name__ == "__main__":
    asyncio.run(reproduce_500())
