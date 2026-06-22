from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from routers.vela_auth import get_api_key
import services.vela_state as state

router = APIRouter()


class ForwardRequest(BaseModel):
    method: str
    path: str
    headers: dict | None = None
    query_params: dict | None = None
    body: str | None = None


@router.post("/agents/{agent_id}/forward", dependencies=[Depends(get_api_key)])
async def forward(agent_id: str, payload: ForwardRequest):
    if state.settings is None or state.forwarder is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")

    result = await state.forwarder.forward(
        agent_id=agent_id,
        method=payload.method,
        path=payload.path,
        headers=payload.headers,
        query_params=payload.query_params,
        body=payload.body,
    )

    response_headers = {k: v for k, v in result["headers"].items() if k.lower() not in {"content-length", "transfer-encoding", "connection"}}
    return Response(content=result["body"], status_code=result["status_code"], headers=response_headers)
