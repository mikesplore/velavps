import asyncio
import base64
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException, status

from services.agent_registry import AgentRegistry, AgentConnection
from services.settings import Settings


class Forwarder:
    def __init__(self, settings: Settings, registry: AgentRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self._client = httpx.AsyncClient(timeout=settings.vps.default_agent_timeout_seconds)

    async def forward(
        self,
        agent_id: str,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
        body: Optional[str] = None,
    ) -> Dict[str, Any]:
        agent = await self.registry.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

        headers = headers or {}
        headers["X-VPS-Auth"] = self.settings.vps.agent_shared_secret

        if agent.websocket is not None:
            return await self._forward_via_websocket(agent, method, path, headers, query_params or {}, body)

        if self.settings.vps.allow_direct_agent_forwarding and agent.public_address:
            return await self._forward_via_http(agent.public_address, method, path, headers, query_params or {}, body)

        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agent is not connected")

    async def _forward_via_websocket(
        self,
        agent: AgentConnection,
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body: Optional[str],
    ) -> Dict[str, Any]:
        assert agent.websocket is not None

        async with agent.ws_lock:
            request_id = str(id(agent.websocket)) + str(asyncio.get_running_loop().time())
            message = {
                "type": "forward_request",
                "request_id": request_id,
                "method": method,
                "path": path,
                "query_params": query_params,
                "headers": headers,
                "body": base64.b64encode(body.encode("utf-8") if body else b"").decode("utf-8"),
            }

            future = asyncio.get_running_loop().create_future()
            async with agent.pending_lock:
                agent.pending_responses[request_id] = future

            try:
                await agent.websocket.send_json(message)
                response = await asyncio.wait_for(future, timeout=self.settings.vps.default_agent_timeout_seconds)
            except asyncio.TimeoutError:
                async with agent.pending_lock:
                    agent.pending_responses.pop(request_id, None)
                raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Agent forward timed out")
            finally:
                async with agent.pending_lock:
                    agent.pending_responses.pop(request_id, None)

            if response.get("type") != "forward_response" or response.get("request_id") != request_id:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Invalid agent response")

            return {
                "status_code": response.get("status_code", 502),
                "headers": response.get("headers", {}),
                "body": base64.b64decode(response.get("body", "")).decode("utf-8", errors="replace"),
            }

    async def _forward_via_http(
        self,
        public_address: str,
        method: str,
        path: str,
        headers: Dict[str, str],
        query_params: Dict[str, str],
        body: Optional[str],
    ) -> Dict[str, Any]:
        url = public_address.rstrip("/") + path
        response = await self._client.request(method, url, headers=headers, params=query_params, content=body or "")
        return {
            "status_code": response.status_code,
            "headers": {k: v for k, v in response.headers.items()},
            "body": response.text,
        }
