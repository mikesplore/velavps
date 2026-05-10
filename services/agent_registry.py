from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import WebSocket


@dataclass
class AgentConnection:
    agent_id: str
    public_address: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    websocket: Optional[WebSocket] = None
    connected: bool = False
    ws_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_responses: Dict[str, asyncio.Future] = field(default_factory=dict)
    pending_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_seen = datetime.now(timezone.utc)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "public_address": self.public_address,
            "metadata": self.metadata,
            "last_seen": self.last_seen.isoformat() + "Z",
            "connected": self.connected,
        }


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: Dict[str, AgentConnection] = {}
        self._lock = asyncio.Lock()

    async def register_agent(
        self,
        agent_id: str,
        public_address: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentConnection:
        metadata = metadata or {}
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                agent = AgentConnection(agent_id=agent_id, public_address=public_address, metadata=metadata)
                self._agents[agent_id] = agent
            else:
                agent.public_address = public_address or agent.public_address
                agent.metadata.update(metadata)
            agent.touch()
            return agent

    async def heartbeat_agent(self, agent_id: str) -> AgentConnection | None:
        async with self._lock:
            agent = self._agents.get(agent_id)
            if not agent:
                return None
            agent.touch()
            return agent

    async def get_agent(self, agent_id: str) -> AgentConnection | None:
        async with self._lock:
            return self._agents.get(agent_id)

    async def list_agents(self) -> list[Dict[str, Any]]:
        async with self._lock:
            return [agent.as_dict() for agent in self._agents.values()]

    async def set_websocket_connection(self, agent_id: str, websocket: WebSocket) -> AgentConnection:
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                agent = AgentConnection(agent_id=agent_id)
                self._agents[agent_id] = agent
            agent.websocket = websocket
            agent.connected = True
            agent.touch()
            return agent

    async def remove_websocket_connection(self, agent_id: str) -> None:
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return
            agent.websocket = None
            agent.connected = False
            async with agent.pending_lock:
                for future in agent.pending_responses.values():
                    if not future.done():
                        future.set_exception(RuntimeError("Agent connection closed"))
                agent.pending_responses.clear()
