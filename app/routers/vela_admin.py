"""
Admin endpoints have been removed to prevent IDOR vulnerability.
All user management is now handled automatically through the registration endpoint.
Secrets are never exposed through admin APIs.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/secrets")
async def list_secrets_disabled():
    """
    Admin endpoints have been removed for security.
    User secrets cannot be listed or managed through admin APIs.
    """
    return {"message": "Admin endpoints disabled for security"}


@router.post("/secrets")
async def create_secret_disabled():
    """
    Admin endpoints have been removed for security.
    User secrets are created through the /register endpoint.
    """
    return {"message": "Admin endpoints disabled for security"}


@router.delete("/secrets/{secret}")
async def revoke_secret_disabled(secret: str):
    """
    Admin endpoints have been removed for security.
    User secrets can only be revoked by the user through re-registration.
    """
    return {"message": "Admin endpoints disabled for security"}


@router.get("/stats")
async def get_stats_disabled():
    """
    Admin endpoints have been removed for security.
    """
    return {"message": "Admin endpoints disabled for security"}


