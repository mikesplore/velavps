"""
Authentication module for Vela multi-tenant relay.

Implements secret-as-identity model:
- Each user has a unique secret
- Secret is used for both agent registration and client API access
- Secret is passed via X-Secret header
"""

from fastapi import Header, HTTPException, status

from app.services import vela_state as state


def get_secret(x_secret: str | None = Header(None, alias="X-Secret")) -> str:
    """
    Extract and validate user secret from X-Secret header.
    
    The secret serves as both:
    - Authentication credential
    - User identity
    
    For agent registration: secret must exist in database
    For client requests: secret must match the agent's registered secret
    
    Note: This function only extracts the secret. 
    Registration endpoint validates secret existence.
    Relay endpoint validates secret matches agent ownership.
    """
    if state.settings is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    if not x_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-Secret header")
    
    return x_secret


def get_admin_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> str:
    """
    Extract and validate admin API key from X-API-Key header.
    
    Admin API keys are for management endpoints (e.g., creating secrets).
    These are different from user secrets.
    """
    if state.settings is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header")
    
    if x_api_key not in state.settings.vps.api_keys:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")
    
    return x_api_key