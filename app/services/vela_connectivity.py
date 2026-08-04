"""Agent tunnel connectivity tracking and push alerts when the PC agent goes offline."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.services import vela_state as state
from app.services import vela_push

logger = logging.getLogger(__name__)


@dataclass
class _AgentConnectivityState:
    connected: bool = False
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    offline_task: asyncio.Task | None = None
    offline_notified: bool = False


class ConnectivityMonitor:
    def __init__(self) -> None:
        self._states: dict[str, _AgentConnectivityState] = {}
        self._lock = asyncio.Lock()

    async def agent_status(self, agent_id: str) -> dict[str, Any]:
        registry_agent = await state.registry.get_agent(agent_id) if state.registry else None
        connected = bool(registry_agent and registry_agent.connected and registry_agent.websocket is not None)
        last_seen = registry_agent.last_seen if registry_agent else datetime.now(UTC)
        async with self._lock:
            tracked = self._states.get(agent_id)
            if tracked is not None:
                tracked.connected = connected
                tracked.last_seen = last_seen
        return {
            "agent_id": agent_id,
            "connected": connected,
            "relay_ready": connected or bool(registry_agent and registry_agent.public_address),
            "last_seen": last_seen.isoformat().replace("+00:00", "Z"),
        }

    async def on_agent_connected(self, agent_id: str) -> None:
        async with self._lock:
            tracked = self._states.setdefault(agent_id, _AgentConnectivityState())
            was_offline_notified = tracked.offline_notified
            if tracked.offline_task and not tracked.offline_task.done():
                tracked.offline_task.cancel()
            tracked.offline_task = None
            tracked.connected = True
            tracked.last_seen = datetime.now(UTC)
            tracked.offline_notified = False

        if was_offline_notified:
            label = vela_push.agent_label(agent_id)
            await self._send_connectivity_push(
                agent_id=agent_id,
                title=f"{label} back online",
                body=f"{label} is reachable again.",
                status="online",
            )

    async def on_agent_disconnected(self, agent_id: str) -> None:
        delay = 30
        if state.settings is not None:
            delay = max(5, int(state.settings.vps.connectivity_offline_delay_seconds))

        async with self._lock:
            tracked = self._states.setdefault(agent_id, _AgentConnectivityState())
            tracked.connected = False
            tracked.last_seen = datetime.now(UTC)
            if tracked.offline_task and not tracked.offline_task.done():
                tracked.offline_task.cancel()
            tracked.offline_task = asyncio.create_task(self._offline_after_delay(agent_id, delay))

    async def _offline_after_delay(self, agent_id: str, delay_seconds: int) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return

        registry_agent = await state.registry.get_agent(agent_id) if state.registry else None
        if registry_agent and registry_agent.connected and registry_agent.websocket is not None:
            return

        async with self._lock:
            tracked = self._states.setdefault(agent_id, _AgentConnectivityState())
            if tracked.connected:
                return
            tracked.offline_notified = True

        label = vela_push.agent_label(agent_id)
        await self._send_connectivity_push(
            agent_id=agent_id,
            title=f"{label} unreachable",
            body=f"{label} is offline. Remote control is unavailable until it reconnects.",
            status="offline",
        )

    async def _send_connectivity_push(self, *, agent_id: str, title: str, body: str, status: str) -> None:
        if not vela_push.is_configured():
            logger.info("Skipping connectivity push for %s: FCM not configured", agent_id)
            return
        try:
            delivered = await asyncio.to_thread(
                vela_push.send_push,
                agent_id=agent_id,
                title=title,
                body=body,
                data={
                    "source": "vela",
                    "alert_type": "agent_connectivity",
                    "status": status,
                },
            )
            logger.info("Connectivity push (%s) for %s delivered to %d device(s)", status, agent_id, delivered)
        except Exception as exc:
            logger.warning("Connectivity push failed for %s: %s", agent_id, exc)


monitor = ConnectivityMonitor()
