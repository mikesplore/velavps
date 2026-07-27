import pytest
from fastapi.testclient import TestClient

from main import app
from app.services import vela_state as state

client = TestClient(app)


@pytest.fixture(autouse=True)
def cleanup_db():
    if state.db:
        conn = state.db._get_connection()
        conn.execute("DELETE FROM push_devices")
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
        conn.execute("DELETE FROM push_devices")
        conn.execute("DELETE FROM agent_credentials")
        conn.execute("DELETE FROM app_agent_links")
        conn.execute("DELETE FROM agent_pairing_sessions")
        conn.execute("DELETE FROM ws_tokens")
        conn.execute("DELETE FROM agents")
        conn.execute("DELETE FROM secrets")
        conn.execute("DELETE FROM audit_events")


def _pair_agent(fingerprint: str) -> tuple[str, str]:
    start = client.post(
        "/agents/register/start",
        json={"agent_name": "push-test", "device_info": {"device_fingerprint": fingerprint}},
    )
    assert start.status_code == 200
    pairing_code = start.json()["pairing_code"]
    pairing_pin = start.json()["pairing_pin"]

    paired = client.post(
        "/pair/complete",
        json={"pairing_code": pairing_code, "pairing_pin": pairing_pin, "agent_label": "Phone"},
    )
    assert paired.status_code == 200
    return paired.json()["agent_id"], paired.json()["relay_secret"]


def test_relay_push_device_registration_is_local_not_forwarded(monkeypatch):
    agent_id, secret = _pair_agent("fp-push-register")

    forwarded = {"called": False}

    async def fake_forward(*args, **kwargs):
        forwarded["called"] = True
        return {"status_code": 200, "headers": {}, "body": b"{}"}

    monkeypatch.setattr(state.forwarder, "forward", fake_forward)

    response = client.post(
        f"/relay/{agent_id}/push/devices",
        headers={"X-Secret": secret},
        json={"token": "b" * 32, "installation_id": "pixel"},
    )
    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert forwarded["called"] is False
    devices = state.db.list_push_devices(agent_id)
    assert len(devices) == 1
    assert devices[0]["installation_id"] == "pixel"


def test_relay_status_reports_disconnected_by_default():
    agent_id, secret = _pair_agent("fp-status")

    response = client.get(
        f"/relay/{agent_id}/status",
        headers={"X-Secret": secret},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_id"] == agent_id
    assert payload["connected"] is False
    assert payload["relay_ready"] is False


def test_agent_push_send_requires_fcm_config(monkeypatch):
    agent_id, secret = _pair_agent("fp-send")
    state.db.upsert_push_device(agent_id=agent_id, token="c" * 32)

    monkeypatch.setattr("app.services.vela_push.get_configuration_error", lambda: "FCM not configured")

    response = client.post(
        f"/agents/{agent_id}/push/send",
        headers={"X-Secret": secret},
        json={"title": "Test", "body": "Hello", "data": {"source": "test"}},
    )
    assert response.status_code == 503


def test_relay_push_send_delivers_via_service(monkeypatch):
    agent_id, secret = _pair_agent("fp-relay-send")

    sent = []

    def fake_send_push(**kwargs):
        sent.append(kwargs)
        return 2

    monkeypatch.setattr("app.services.vela_push.get_configuration_error", lambda: None)
    monkeypatch.setattr("app.services.vela_push.send_push", fake_send_push)

    response = client.post(
        f"/relay/{agent_id}/push/send",
        headers={"X-Secret": secret},
        json={"title": "Hi", "body": "There", "data": {"source": "vela"}},
    )
    assert response.status_code == 200
    assert response.json()["delivered"] == 2
    assert sent[0]["agent_id"] == agent_id


def test_send_push_includes_agent_metadata(monkeypatch):
    agent_id, _secret = _pair_agent("fp-metadata")
    state.db.upsert_push_device(agent_id=agent_id, token="d" * 32)

    sent = []

    class FakeNotification:
        def __init__(self, **kwargs):
            self.title = kwargs.get("title")
            self.body = kwargs.get("body")

    class FakeMessage:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

    class FakeMessaging:
        Notification = FakeNotification
        Message = FakeMessage

        @staticmethod
        def send(message):
            sent.append(message)
            return "msg-id"

    monkeypatch.setattr("app.services.vela_push._get_messaging", lambda: FakeMessaging())

    from app.services import vela_push

    delivered = vela_push.send_push(
        agent_id=agent_id,
        title="Alert",
        body="Something happened",
        data={"source": "vela", "alert_type": "custom"},
    )
    assert delivered == 1
    assert len(sent) == 1
    notification = sent[0]._kwargs["notification"]
    assert notification.title == "Vela · Phone · Alert"
    data = sent[0]._kwargs["data"]
    assert data["agent_id"] == agent_id
    assert data["display_name"] == "Phone"
    assert data["source"] == "vela"
    assert data["alert_type"] == "custom"


def test_connectivity_push_uses_agent_label(monkeypatch):
    agent_id, _secret = _pair_agent("fp-connectivity")

    captured = []

    def fake_send_push(**kwargs):
        captured.append(kwargs)
        return 1

    monkeypatch.setattr("app.services.vela_push.is_configured", lambda: True)
    monkeypatch.setattr("app.services.vela_push.send_push", fake_send_push)

    from app.services.vela_connectivity import ConnectivityMonitor, _AgentConnectivityState
    import asyncio

    monitor = ConnectivityMonitor()

    async def run():
        monitor._states[agent_id] = _AgentConnectivityState(offline_notified=True)
        await monitor.on_agent_connected(agent_id)

        monitor._states[agent_id] = _AgentConnectivityState(connected=False, offline_notified=False)
        await monitor._offline_after_delay(agent_id, 0)

    asyncio.run(run())

    assert len(captured) == 2
    online, offline = captured
    assert online["title"] == "Vela · Phone back online"
    assert online["body"] == "Phone is reachable again."
    assert offline["title"] == "Vela · Phone unreachable"
    assert offline["body"] == "Phone is offline. Remote control is unavailable until it reconnects."
    assert online["data"]["alert_type"] == "agent_connectivity"
    assert offline["data"]["status"] == "offline"


def test_format_push_title():
    from app.services import vela_push

    agent_id = "agt_test"

    class FakeDb:
        def get_agent_by_id(self, _agent_id):
            class Agent:
                display_name = "Work PC"
            return Agent()

    original_db = state.db
    state.db = FakeDb()
    try:
        assert vela_push.format_push_title(agent_id, "Vela alert · HighCPU") == "Vela · Work PC · HighCPU"
        assert vela_push.format_push_title(agent_id, "Vela · Work PC back online") == "Vela · Work PC back online"
        assert vela_push.format_push_title(agent_id, "Custom") == "Vela · Work PC · Custom"
    finally:
        state.db = original_db
