from fastapi import Header, HTTPException, status

import services.state as state


def get_api_key(x_api_key: str | None = Header(None)) -> str:
    if state.settings is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")
    if x_api_key not in state.settings.vps.api_keys:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    return x_api_key


def get_agent_token(x_agent_token: str | None = Header(None)) -> str:
    if state.settings is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    if not x_agent_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-Agent-Token header")
    if x_agent_token != state.settings.vps.agent_shared_secret:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid agent token")
    return x_agent_token
