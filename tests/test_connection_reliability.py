import asyncio

import pytest
from fastapi import HTTPException

from services.vela_agent_registry import AgentRegistry
from services.vela_forwarder import Forwarder
from services.vela_settings import Settings, VPSSettings


def _forwarder(wait_seconds: int = 1) -> tuple[Forwarder, AgentRegistry]:
    registry = AgentRegistry()
    settings = Settings(vps=VPSSettings(
        agent_connect_wait_seconds=wait_seconds,
        allow_direct_agent_forwarding=False,
    ))
    return Forwarder(settings, registry), registry


def test_known_disconnected_agent_waits_for_reconnect():
    async def scenario():
        forwarder, registry = _forwarder()
        agent = await registry.register_agent("reconnecting")
        resolving = asyncio.create_task(forwarder._resolve_agent("reconnecting"))
        await asyncio.sleep(0.02)
        await registry.set_websocket_connection("reconnecting", object())
        assert await resolving is agent

    asyncio.run(scenario())


def test_known_disconnected_agent_times_out_with_retry_after():
    async def scenario():
        forwarder, registry = _forwarder(wait_seconds=0)
        await registry.register_agent("offline")
        with pytest.raises(HTTPException) as caught:
            await forwarder._resolve_agent("offline")
        assert caught.value.status_code == 503
        assert caught.value.headers.get("Retry-After") == "1"

    asyncio.run(scenario())


def test_stale_socket_removal_does_not_clear_replacement():
    async def scenario():
        registry = AgentRegistry()
        await registry.register_agent("raced")
        old_socket = object()
        new_socket = object()
        await registry.set_websocket_connection("raced", old_socket)
        agent = await registry.set_websocket_connection("raced", new_socket)
        await registry.remove_websocket_connection("raced", old_socket)
        assert agent.websocket is new_socket
        assert agent.connected is True
        assert agent.connected_event.is_set()

    asyncio.run(scenario())
