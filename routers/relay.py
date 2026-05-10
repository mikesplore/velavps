import base64
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi import status as http_status
from pydantic import BaseModel

from routers.auth import get_api_key
import services.state as state

router = APIRouter()


class RegisterRequest(BaseModel):
    agent_id: str
    secret: str
    public_address: str | None = None
    metadata: dict | None = None


class RelayRequest(BaseModel):
    method: str
    headers: dict | None = None
    query_params: dict | None = None
    body: str | None = None


@router.post("/register")
async def register_agent(payload: RegisterRequest):
    if state.settings is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")

    if payload.secret != state.settings.vps.agent_shared_secret:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="Invalid agent secret")

    agent = await state.registry.register_agent(payload.agent_id, payload.public_address, payload.metadata)
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    await state.registry.set_agent_ws_token(agent.agent_id, token, expiry)

    return {
        "agent": agent.as_dict(),
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


@router.post("/relay/{agent_id}/{path:path}", dependencies=[Depends(get_api_key)])
async def relay_request(agent_id: str, path: str, request: Request):
    if state.settings is None or state.forwarder is None:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")

    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8", errors="replace") if body_bytes else None
    headers = {k: v for k, v in request.headers.items()}
    query_params = dict(request.query_params)

    result = await state.forwarder.forward(
        agent_id=agent_id,
        method=request.method,
        path=f"/{path}",
        headers=headers,
        query_params=query_params,
        body=body_text,
    )

    response_headers = {k: v for k, v in result["headers"].items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=result["body"], status_code=result["status_code"], headers=response_headers)
