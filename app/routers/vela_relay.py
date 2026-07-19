import hashlib
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi import status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .vela_auth import get_secret
from app.services import vela_state as state
from app.services.vela_database import ConflictError
from app.services.vela_forwarder import decode_chunk_body

router = APIRouter()
logger = logging.getLogger(__name__)
API_VERSION = "2026-07-pairing-v1"


class InMemoryRateLimiter:
    def __init__(self):
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, max_hits: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        with self._lock:
            timestamps = [ts for ts in self._events.get(key, []) if ts >= cutoff]
            if len(timestamps) >= max_hits:
                self._events[key] = timestamps
                return False
            timestamps.append(now)
            self._events[key] = timestamps
            return True


rate_limiter = InMemoryRateLimiter()
metrics = {
    "register_started": 0,
    "pairing_completed": 0,
    "pairing_invalid_or_expired": 0,
    "agent_activated": 0,
}


class RegisterRequest(BaseModel):
    agent_id: str
    public_address: str | None = None
    metadata: dict | None = None


class RelayRequest(BaseModel):
    method: str
    headers: dict | None = None
    query_params: dict | None = None
    body: str | None = None


class RegisterStartRequest(BaseModel):
    agent_name: str
    device_info: dict | None = None
    tenant_hint: str | None = None
    agent_id: str | None = None


class PairCompleteRequest(BaseModel):
    pairing_code: str
    pairing_pin: str
    agent_label: str | None = None


class ActivateRequest(BaseModel):
    agent_id: str
    activation_token: str


def _request_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/agents/register/start")
async def register_start(payload: RegisterStartRequest, request: Request):
    if state.settings is None or state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")

    fingerprint = (payload.device_info or {}).get("device_fingerprint", "none")
    rate_key = f"register_start:{_request_ip(request)}:{fingerprint}"
    if not rate_limiter.hit(rate_key, max_hits=20, window_seconds=60):
        raise HTTPException(status_code=http_status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limit_exceeded")

    session = state.db.create_or_refresh_pairing_session(
        agent_name=payload.agent_name,
        device_info=payload.device_info,
        tenant_hint=payload.tenant_hint,
        pairing_ttl_seconds=state.settings.vps.pairing_code_ttl_seconds,
        existing_agent_id=payload.agent_id,
    )
    metrics["register_started"] += 1
    logger.info("agent_register_started agent_id=%s", session["agent_id"])
    return {
        "api_version": API_VERSION,
        "agent_id": session["agent_id"],
        "pairing_code": session["pairing_code"],
        "pairing_pin": session["pairing_pin"],
        "pairing_expires_in": session["pairing_expires_in"],
        "pairing_qr_payload": f"vela://pair?code={session['pairing_code']}&agent_id={session['agent_id']}",
    }


@router.get("/agents/register/status")
async def register_status(agent_id: str):
    if state.settings is None or state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    try:
        status_payload = state.db.get_registration_status(
            agent_id=agent_id,
            activation_ttl_seconds=state.settings.vps.activation_token_ttl_seconds,
        )
    except ValueError:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Agent not found")
    registry_agent = await state.registry.get_agent(agent_id) if state.registry else None
    status_payload["relay_ready"] = bool(
        registry_agent and (registry_agent.websocket is not None or bool(registry_agent.public_address))
    )
    return {"api_version": API_VERSION, **status_payload}


@router.post("/pair/complete")
async def pair_complete(payload: PairCompleteRequest, request: Request):
    if state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")

    pair_key = f"pair_complete:{_request_ip(request)}:{hashlib.sha1(payload.pairing_code.encode('utf-8')).hexdigest()[:8]}"
    ip_key = f"pair_complete_ip:{_request_ip(request)}"
    if not rate_limiter.hit(pair_key, max_hits=8, window_seconds=300) or not rate_limiter.hit(ip_key, max_hits=50, window_seconds=300):
        raise HTTPException(status_code=http_status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limit_exceeded")

    try:
        result = state.db.complete_pairing(payload.pairing_code, payload.pairing_pin, payload.agent_label)
    except ValueError:
        metrics["pairing_invalid_or_expired"] += 1
        logger.warning("pairing invalid_or_expired_code ip=%s", _request_ip(request))
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid_or_expired_code")

    metrics["pairing_completed"] += 1
    logger.info("pairing_completed agent_id=%s", result["agent_id"])
    relay_base_url = f"{str(request.base_url).rstrip('/')}/relay/{result['agent_id']}"
    return {
        "status": "paired",
        "agent_id": result["agent_id"],
        "relay_base_url": relay_base_url,
        "idempotent": result["idempotent"],
        "relay_secret": result["relay_secret"],
        "relay_secret_shared": result["relay_secret_shared"],
    }


@router.post("/agents/register/activate")
async def register_activate(payload: ActivateRequest):
    if state.settings is None or state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    try:
        result = state.db.activate_agent(
            payload.agent_id,
            payload.activation_token,
            ttl_seconds=state.settings.vps.activation_token_ttl_seconds,
        )
    except ValueError as exc:
        if str(exc) == "secret_already_delivered":
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="secret_already_delivered")
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid_activation_token")
    metrics["agent_activated"] += 1
    logger.info("agent_activated agent_id=%s", payload.agent_id)
    return {"api_version": API_VERSION, **result}


@router.post("/agents/{agent_id}/revoke", dependencies=[Depends(get_secret)])
async def revoke_agent(agent_id: str, secret: str = Depends(get_secret)):
    if state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    owned_agent = state.db.get_agent(agent_id, secret)
    if not owned_agent:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Agent not found")
    revoked_count = state.db.revoke_agent_credentials(agent_id, revoked_by=secret)
    return {"agent_id": agent_id, "revoked_credentials": revoked_count}


@router.post("/register")
async def register_agent(payload: RegisterRequest):
    """
    Register a new agent. This is a one-time operation per agent_id.
    
    The server auto-generates a secret for the agent on first registration.
    This secret is stored in the database and returned to the agent ONCE.
    The agent must use this secret (via X-Secret header) for subsequent
    requests (heartbeat, relay, etc.).
    
    NOTE: This endpoint cannot be called again with the same agent_id.
    - If agent_id is already registered → 409 Conflict
    - If you lost the secret, you must use a different agent_id
    """
    if state.settings is None or state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    if not state.settings.vps.legacy_registration_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_410_GONE,
            detail="legacy_registration_disabled",
        )

    # Check if this agent_id already exists (NO re-registration allowed)
    existing_agent = state.db.get_agent_by_id(payload.agent_id)
    if existing_agent:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Agent ID '{payload.agent_id}' is already registered. Each agent_id can only be registered once. Use a different agent_id."
        )

    # New agent — generate a fresh secret and store it
    secret = secrets.token_urlsafe(32)
    state.db.create_secret(secret)

    try:
        # Register agent with database
        agent = state.db.register_agent(
            agent_id=payload.agent_id,
            secret=secret,
            public_address=payload.public_address,
            metadata=payload.metadata,
        )
    except ValueError as e:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(e))

    # Generate WebSocket token for this agent
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    await state.registry.set_websocket_connection(agent.agent_id, None)
    await state.registry.set_agent_ws_token(agent.agent_id, token, expiry)

    # Return secret ONLY on first registration
    return {
        "api_version": API_VERSION,
        "agent": agent.as_dict(),
        "secret": secret,
        "ws_token": token,
        "expires_at": expiry.isoformat() + "Z",
    }


@router.websocket("/tunnel")
async def agent_tunnel(websocket: WebSocket, agent_id: str, token: str | None = None):
    if state.settings is None:
        await websocket.close(code=http_status.WS_1011_INTERNAL_ERROR)
        return

    if not token:
        token = websocket.headers.get("x-agent-token")

    if not token or not await state.registry.validate_agent_ws_token(agent_id, token):
        await websocket.close(code=http_status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    agent = await state.registry.set_websocket_connection(agent_id, websocket)

    try:
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            if message_type == "heartbeat":
                agent.touch()
                continue

            if message_type == "forward_response":
                request_id = message.get("request_id")
                async with agent.pending_lock:
                    future = agent.pending_responses.pop(request_id, None)
                if future is not None and not future.done():
                    future.set_result(message)
                continue

            if message_type == "forward_response_start":
                request_id = message.get("request_id")
                async with agent.pending_lock:
                    session = agent.pending_streams.get(request_id)
                if session is not None:
                    session.status_code = int(message.get("status_code", 502))
                    session.headers = message.get("headers") or {}
                    session.started.set()
                continue

            if message_type == "forward_response_chunk":
                request_id = message.get("request_id")
                async with agent.pending_lock:
                    session = agent.pending_streams.get(request_id)
                if session is not None:
                    await session.chunks.put(decode_chunk_body(message))
                continue

            if message_type == "forward_response_end":
                request_id = message.get("request_id")
                async with agent.pending_lock:
                    session = agent.pending_streams.pop(request_id, None)
                if session is not None:
                    if not session.started.is_set():
                        session.started.set()
                    await session.chunks.put(None)
                continue
    except WebSocketDisconnect:
        await state.registry.remove_websocket_connection(agent_id)
    except Exception:
        await state.registry.remove_websocket_connection(agent_id)


@router.get("/relay/{agent_id}/callback")
async def spotify_callback(agent_id: str, request: Request):
    """
    Public callback endpoint for OAuth providers (e.g. Spotify).
    
    This endpoint intentionally skips authentication because OAuth callbacks
    come from external providers and cannot carry relay auth headers.
    Security is provided by the short-lived, single-use `code` parameter
    from the OAuth flow.
    
    Forwards the callback (with all query params including `code`) to the agent.
    """
    if state.settings is None or state.db is None or state.forwarder is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")

    # Verify agent exists (no secret check — public callback)
    agent = state.db.get_agent_by_id(agent_id)
    if not agent:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Agent not found")

    query_params = dict(request.query_params)
    callback_path = "/callback"

    result = await state.forwarder.forward(
        agent_id=agent_id,
        method="GET",
        path=callback_path,
        headers={},
        query_params=query_params,
        body=None,
    )

    response_headers = {k: v for k, v in result["headers"].items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=result["body"], status_code=result["status_code"], headers=response_headers)


@router.api_route(
    "/relay/{agent_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    dependencies=[Depends(get_secret)],
)
async def relay_request(agent_id: str, path: str, request: Request, secret: str = Depends(get_secret)):
    """
    Relay requests to a registered agent.
    
    Isolation enforcement:
    - Client's secret must match the agent's registered secret
    - Cross-user access is strictly forbidden
    """
    if state.settings is None or state.db is None or state.forwarder is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")

    # Check agent exists and belongs to this secret (isolation check)
    agent = state.db.get_agent(agent_id, secret)
    if not agent:
        # Check if agent exists but belongs to someone else
        existing_agent = state.db.get_agent_by_id(agent_id)
        if existing_agent:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Access denied: this agent belongs to another user"
            )
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Agent not found")

    body_bytes = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    query_params = dict(request.query_params)
    relay_path = f"/{path}"
    stream = request.method == "POST" and path.rstrip("/").endswith("assistant/stream")

    if stream:
        status_code, stream_headers, body_iter = await state.forwarder.forward_stream(
            agent_id=agent_id,
            method=request.method,
            path=relay_path,
            headers=headers,
            query_params=query_params,
            body=body_bytes or None,
        )
        response_headers = {
            k: v
            for k, v in stream_headers.items()
            if k.lower() not in {"content-length", "transfer-encoding", "connection"}
        }
        return StreamingResponse(
            body_iter,
            status_code=status_code,
            media_type=stream_headers.get("content-type", "text/event-stream"),
            headers=response_headers,
        )

    result = await state.forwarder.forward(
        agent_id=agent_id,
        method=request.method,
        path=relay_path,
        headers=headers,
        query_params=query_params,
        body=body_bytes or None,
    )

    response_headers = {k: v for k, v in result["headers"].items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=result["body"], status_code=result["status_code"], headers=response_headers)


@router.get("/agents")
async def list_agents(secret: str = Depends(get_secret)):
    """List all agents registered by this user (secret)."""
    if state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    agents = state.db.list_agents(secret)
    return {"agents": [agent.as_dict() for agent in agents]}


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, secret: str = Depends(get_secret)):
    """Get details of a specific agent."""
    if state.db is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    agent = state.db.get_agent(agent_id, secret)
    if not agent:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    return {"agent": agent.as_dict()}


@router.post("/agents/{agent_id}/ws-token")
async def reissue_ws_token(agent_id: str, secret: str = Depends(get_secret)):
    """
    Re-issue a WebSocket token for an existing agent.
    Returns a fresh short-lived token (60s) for connecting the tunnel.
    Use this when reconnecting after a disconnect.
    """
    if state.db is None or state.registry is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    # Verify agent exists and belongs to this secret
    agent = state.db.get_agent(agent_id, secret)
    if not agent:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Agent not found in the database")
    
    # Generate new token
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    await state.registry.set_agent_ws_token(agent_id, token, expiry)
    
    return {
        "ws_token": token,
        "expires_at": expiry.isoformat() + "Z",
    }
