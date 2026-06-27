import base64
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi import status as http_status
from pydantic import BaseModel

from .vela_auth import get_secret
from app.services import vela_state as state
from app.services.vela_database import ConflictError

router = APIRouter()


class RegisterRequest(BaseModel):
    agent_id: str
    public_address: str | None = None
    metadata: dict | None = None


class RelayRequest(BaseModel):
    method: str
    headers: dict | None = None
    query_params: dict | None = None
    body: str | None = None


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
    except WebSocketDisconnect:
        await state.registry.remove_websocket_connection(agent_id)
    except Exception:
        await state.registry.remove_websocket_connection(agent_id)


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

    result = await state.forwarder.forward(
        agent_id=agent_id,
        method=request.method,
        path=f"/{path}",
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
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Agent not found")
    
    # Generate new token
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    await state.registry.set_agent_ws_token(agent_id, token, expiry)
    
    return {
        "ws_token": token,
        "expires_at": expiry.isoformat() + "Z",
    }
