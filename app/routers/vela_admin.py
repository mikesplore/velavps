"""
Admin endpoints for Vela multi-tenant relay.

Provides management functionality:
- Create new user secrets
- List all users/secrets
- Revoke secrets
"""
import secrets as secret_generator
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .vela_auth import get_admin_api_key
from app.services import vela_state as state
from app.services.vela_database import ConflictError

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateSecretRequest(BaseModel):
    """Request to create a new user secret."""
    secret: Optional[str] = None  # If not provided, a random one will be generated


class CreateSecretResponse(BaseModel):
    """Response with created secret."""
    secret: str
    message: str


class SecretInfo(BaseModel):
    """Information about a secret."""
    secret: str
    agent_count: int


class ListSecretsResponse(BaseModel):
    """Response with list of secrets."""
    secrets: List[SecretInfo]
    total: int


@router.post("/secrets", response_model=CreateSecretResponse)
async def create_secret(
    request: CreateSecretRequest,
    admin_key: str = Depends(get_admin_api_key)
):
    """
    Create a new user secret.
    
    If no secret is provided in the request, a cryptographically secure
    random secret will be generated.
    
    Requires admin API key authentication.
    """
    if state.db is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    # Generate a random secret if not provided
    if not request.secret:
        secret = secret_generator.token_urlsafe(32)  # 256-bit entropy
    else:
        secret = request.secret
    
    # Create the secret in database
    created = state.db.create_secret(secret)
    if not created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Secret already exists"
        )
    
    return CreateSecretResponse(
        secret=secret,
        message="Secret created successfully. Use this secret as X-Secret header for both agent registration and client requests."
    )


@router.get("/secrets", response_model=ListSecretsResponse)
async def list_secrets(admin_key: str = Depends(get_admin_api_key)):
    """
    List all user secrets and their agent counts.
    
    Requires admin API key authentication.
    """
    if state.db is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    # Get all secrets from database
    conn = state.db._get_connection()
    cursor = conn.execute("""
        SELECT s.secret, COUNT(a.agent_id) as agent_count
        FROM secrets s
        LEFT JOIN agents a ON s.secret = a.secret
        GROUP BY s.secret
        ORDER BY s.created_at DESC
    """)
    
    secrets = []
    for row in cursor:
        secrets.append(SecretInfo(
            secret=row["secret"],
            agent_count=row["agent_count"]
        ))
    
    return ListSecretsResponse(
        secrets=secrets,
        total=len(secrets)
    )


@router.delete("/secrets/{secret}")
async def revoke_secret(
    secret: str,
    admin_key: str = Depends(get_admin_api_key)
):
    """
    Revoke a user secret and delete all associated agents.
    
    This will disconnect all agents using this secret and prevent
    any future registration or client access.
    
    Requires admin API key authentication.
    """
    if state.db is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    # Check if secret exists
    if not state.db.secret_exists(secret):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found")
    
    # Delete all agents with this secret
    conn = state.db._get_connection()
    conn.execute("DELETE FROM agents WHERE secret = ?", (secret,))
    conn.execute("DELETE FROM secrets WHERE secret = ?", (secret,))
    
    return {"message": "Secret revoked successfully"}


@router.get("/stats")
async def get_stats(admin_key: str = Depends(get_admin_api_key)):
    """
    Get system statistics.
    
    Requires admin API key authentication.
    """
    if state.db is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server not configured")
    
    conn = state.db._get_connection()
    
    # Get total secrets count
    total_secrets = conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0]
    
    # Get total agents count
    total_agents = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    
    # Get active agents count
    active_agents = conn.execute("SELECT COUNT(*) FROM agents WHERE status = 'active'").fetchone()[0]
    
    return {
        "total_users": total_secrets,
        "total_agents": total_agents,
        "active_agents": active_agents,
        "inactive_agents": total_agents - active_agents
    }