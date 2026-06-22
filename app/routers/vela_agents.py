from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from routers.vela_auth import get_agent_token, get_api_key
import services.vela_state as state

router = APIRouter()


class AgentRegistrationRequest(BaseModel):
    agent_id: str
    public_address: str | None = None
    metadata: dict | None = None


class AgentHeartbeatRequest(BaseModel):
    agent_id: str


@router.post("/register", dependencies=[Depends(get_agent_token)])
async def register_agent(payload: AgentRegistrationRequest):
    if state.settings is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    agent = await state.registry.register_agent(payload.agent_id, payload.public_address, payload.metadata)
    return {"agent": agent.as_dict()}


@router.post("/heartbeat", dependencies=[Depends(get_agent_token)])
async def heartbeat(payload: AgentHeartbeatRequest):
    if state.settings is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    agent = await state.registry.heartbeat_agent(payload.agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return {"agent": agent.as_dict()}


@router.get("/", dependencies=[Depends(get_api_key)])
async def list_agents():
    agents = await state.registry.list_agents()
    return {"agents": agents}


@router.get("/{agent_id}", dependencies=[Depends(get_api_key)])
async def get_agent(agent_id: str):
    agent = await state.registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return {"agent": agent.as_dict()}


@router.websocket("/ws")
async def agent_ws(websocket: WebSocket, agent_id: str, x_agent_token: str | None = None):
    if state.settings is None:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    if x_agent_token != state.settings.vps.agent_shared_secret:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
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
