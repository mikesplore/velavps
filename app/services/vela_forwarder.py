import asyncio
import base64
import binascii
import uuid
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx
from fastapi import HTTPException, status

from .vela_agent_registry import AgentConnection, AgentRegistry, StreamRelaySession
from .vela_database import VelaDatabase
from .vela_settings import Settings


def decode_chunk_body(message: Dict[str, Any]) -> bytes:
    body = message.get("body", "")
    encoding = message.get("body_encoding")
    if encoding == "base64":
        try:
            return base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError, TypeError):
            return b""
    if encoding == "utf-8" and isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, str):
        return body.encode("utf-8")
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    return b""


class Forwarder:
    def __init__(self, settings: Settings, registry: AgentRegistry, db: Optional[VelaDatabase] = None) -> None:
        self.settings = settings
        self.registry = registry
        self.db = db
        self._client = httpx.AsyncClient(timeout=settings.vps.default_agent_timeout_seconds)
        self._waiter_lock = asyncio.Lock()
        self._waiters: Dict[str, int] = {}

    async def _resolve_agent(self, agent_id: str) -> AgentConnection:
        agent = await self.registry.get_agent(agent_id)
        if not agent:
            agent_exists = bool(self.db and self.db.get_agent_by_id(agent_id))
            if not agent_exists:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
            agent = await self.registry.register_agent(agent_id)
        if agent.websocket is None and not agent.public_address:
            agent = await self._wait_for_agent_connection(agent_id, self.settings.vps.agent_connect_wait_seconds)
        if not agent or (agent.websocket is None and not agent.public_address):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent is connecting. Retry shortly.",
                headers={"Retry-After": str(max(1, self.settings.vps.agent_connect_wait_seconds))},
            )
        return agent

    def _inject_agent_secret(self, agent_id: str, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        headers = dict(headers or {})
        agent_secret = None
        if self.db:
            db_agent = self.db.get_agent_by_id(agent_id)
            if db_agent:
                agent_secret = db_agent.secret
        if agent_secret:
            headers["X-Secret"] = agent_secret
        elif getattr(self.settings.vps, "agent_shared_secret", None):
            headers["X-Secret"] = self.settings.vps.agent_shared_secret
        return headers

    async def forward(
        self,
        agent_id: str,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        agent = await self._resolve_agent(agent_id)
        headers = self._inject_agent_secret(agent_id, headers)

        if self.settings.vps.allow_direct_agent_forwarding and agent.public_address:
            try:
                return await self._forward_via_http(agent.public_address, method, path, headers, query_params or {}, body)
            except httpx.RequestError:
                pass

        if agent.websocket is not None:
            try:
                return await self._forward_via_websocket(agent, method, path, headers, query_params or {}, body)
            except Exception as e:
                if isinstance(e, RuntimeError) and str(e) == "Agent connection closed":
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="agent_connection_lost")
                if isinstance(e, (RuntimeError, asyncio.TimeoutError, HTTPException)):
                    raise
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Websocket forwarding error: {str(e)}")

        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent is not connected")

    async def forward_stream(
        self,
        agent_id: str,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> Tuple[int, Dict[str, str], AsyncIterator[bytes]]:
        """
        Stream a response from the agent over WebSocket using
        forward_response_start / forward_response_chunk / forward_response_end.
        """
        agent = await self._resolve_agent(agent_id)
        headers = self._inject_agent_secret(agent_id, headers)

        if agent.websocket is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent is not connected")

        request_id = uuid.uuid4().hex
        connection_id = agent.connection_id
        session = StreamRelaySession()
        async with agent.pending_lock:
            agent.pending_streams[request_id] = session

        body_payload = self._encode_body_for_websocket(body)
        message = {
            "type": "forward_request",
            "request_id": request_id,
            "connection_id": connection_id,
            "method": method,
            "path": path,
            "query_params": query_params or {},
            "headers": headers,
            **body_payload,
        }

        try:
            async with agent.send_lock:
                await agent.websocket.send_json(message)
            await asyncio.wait_for(
                session.started.wait(),
                timeout=self.settings.vps.default_agent_timeout_seconds,
            )
        except asyncio.TimeoutError:
            async with agent.pending_lock:
                agent.pending_streams.pop(request_id, None)
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Agent stream start timed out")
        except Exception:
            async with agent.pending_lock:
                agent.pending_streams.pop(request_id, None)
            raise

        chunk_timeout = max(self.settings.vps.default_agent_timeout_seconds, 60)

        async def body_iter() -> AsyncIterator[bytes]:
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(session.chunks.get(), timeout=chunk_timeout)
                    except asyncio.TimeoutError:
                        raise HTTPException(
                            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                            detail="Agent stream timed out",
                        )
                    if chunk is None:
                        if session.terminal_error:
                            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=session.terminal_error)
                        break
                    yield chunk
            finally:
                async with agent.pending_lock:
                    agent.pending_streams.pop(request_id, None)

        return session.status_code, session.headers, body_iter()

    async def _wait_for_agent_connection(self, agent_id: str, timeout_seconds: int) -> Optional[AgentConnection]:
        async with self._waiter_lock:
            count = self._waiters.get(agent_id, 0)
            limit = getattr(self.settings.vps, "max_agent_waiters", 32)
            if count >= limit:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="Agent connection queue is full.", headers={"Retry-After": "1"})
            self._waiters[agent_id] = count + 1
        try:
            agent = await self.registry.get_agent(agent_id)
            if agent is None or agent.websocket is not None or agent.public_address:
                return agent
            if timeout_seconds > 0:
                try:
                    await asyncio.wait_for(agent.connected_event.wait(), timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    pass
            return await self.registry.get_agent(agent_id)
        finally:
            async with self._waiter_lock:
                remaining = self._waiters.get(agent_id, 1) - 1
                if remaining > 0:
                    self._waiters[agent_id] = remaining
                else:
                    self._waiters.pop(agent_id, None)

    def _encode_body_for_websocket(self, body: Optional[bytes]) -> Dict[str, Any]:
        if body is None:
            return {"body": None}

        if not isinstance(body, bytes):
            body = str(body).encode("utf-8")

        try:
            decoded = body.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "body": base64.b64encode(body).decode("ascii"),
                "body_encoding": "base64",
            }

        return {"body": decoded, "body_encoding": "utf-8"}

    async def _forward_via_websocket(
        self,
        agent: AgentConnection,
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body: Optional[bytes],
    ) -> Dict[str, Any]:
        assert agent.websocket is not None

        request_id = uuid.uuid4().hex
        connection_id = agent.connection_id
        body_payload = self._encode_body_for_websocket(body)

        message = {
            "type": "forward_request",
            "request_id": request_id,
            "connection_id": connection_id,
            "method": method,
            "path": path,
            "query_params": query_params,
            "headers": headers,
            **body_payload,
        }

        future = asyncio.get_running_loop().create_future()
        async with agent.pending_lock:
            agent.pending_responses[request_id] = future

        try:
            async with agent.send_lock:
                await agent.websocket.send_json(message)
            response = await asyncio.wait_for(future, timeout=self.settings.vps.default_agent_timeout_seconds)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Agent forward timed out")
        finally:
            async with agent.pending_lock:
                agent.pending_responses.pop(request_id, None)

        if (response.get("type") != "forward_response" or response.get("request_id") != request_id
                or (response.get("connection_id") and response.get("connection_id") != connection_id)):
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid agent response")

        body_bytes = decode_chunk_body(response)
        return {
            "status_code": response.get("status_code", 502),
            "headers": response.get("headers", {}),
            "body": body_bytes,
        }

    async def _forward_via_http(
        self,
        public_address: str,
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body: Optional[bytes],
    ) -> Dict[str, Any]:
        url = public_address.rstrip("/") + path
        response = await self._client.request(method, url, headers=headers, params=query_params, content=body or b"")
        return {
            "status_code": response.status_code,
            "headers": {k: v for k, v in response.headers.items()},
            "body": response.content,
        }
