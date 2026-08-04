"""Firebase Cloud Messaging delivery for paired Vela Android devices."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services import vela_state as state

logger = logging.getLogger(__name__)
_firebase_initialized = False


def _validate_service_account_file(path: Path) -> str | None:
    if not path.is_file():
        return f"FCM service account file does not exist: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return f"FCM service account file is not valid JSON: {path}"
    if data.get("type") == "service_account":
        return None
    if "project_info" in data and "client" in data:
        return (
            "fcm_service_account_path points to google-services.json (Android client config). "
            "Use a Firebase Admin service account key instead."
        )
    return 'FCM service account JSON must contain "type": "service_account".'


def get_configuration_error() -> str | None:
    if state.settings is None:
        return "Server not configured"
    path = (state.settings.vps.fcm_service_account_path or "").strip()
    if not path:
        return "FCM service account not configured (set VELAVPS_FCM_SERVICE_ACCOUNT_PATH in .env)"
    return _validate_service_account_file(Path(path).expanduser())


def is_configured() -> bool:
    return get_configuration_error() is None


def register_device(*, agent_id: str, token: str, installation_id: str | None = None) -> None:
    if state.db is None:
        raise RuntimeError("Database not initialized")
    state.db.upsert_push_device(agent_id=agent_id, token=token, installation_id=installation_id)


def unregister_device(*, agent_id: str, token: str) -> bool:
    if state.db is None:
        raise RuntimeError("Database not initialized")
    return state.db.delete_push_device(agent_id=agent_id, token=token)


def agent_label(agent_id: str) -> str:
    """Human-readable name for an agent, for use in notification copy."""
    if state.db is None:
        return agent_id
    agent = state.db.get_agent_by_id(agent_id)
    if agent is None:
        return agent_id
    # Prefer hostname from metadata over display_name
    if agent.metadata and agent.metadata.get("hostname"):
        return agent.metadata["hostname"]
    if agent.display_name:
        return agent.display_name
    return agent_id



def agent_push_context(agent_id: str) -> dict[str, str]:
    """Agent metadata merged into every FCM data payload."""
    context: dict[str, str] = {"agent_id": agent_id}
    if state.db is None:
        return context
    agent = state.db.get_agent_by_id(agent_id)
    if agent is None:
        return context
    if agent.display_name:
        context["display_name"] = agent.display_name
    if agent.tenant_id:
        context["tenant_id"] = agent.tenant_id
    return context


def format_push_title(agent_id: str, title: str) -> str:
    """Prefix notification titles with the agent label for multi-agent phones."""
    label = agent_label(agent_id)
    prefix = f"{label}"
    if title.startswith(prefix):
        return title
    if title.startswith("alert · "):
        return f"{prefix} {title[len('alert · '):]}"
    if title.startswith("resolved · "):
        return f"{prefix} {title[len('resolved · '):]}"
    if title.startswith("· "):
        rest = title[len("· "):]
        if rest.startswith(label):
            return title
        return f"{prefix} {rest}"
    if title.startswith(f"{label} "):
        return title
    return f"{prefix} · {title}"


def send_push(
    *,
    agent_id: str,
    title: str,
    body: str,
    data: dict[str, str],
) -> int:
    messaging = _get_messaging()
    if messaging is None or state.db is None:
        return 0

    payload = agent_push_context(agent_id)
    payload.update({key: str(value) for key, value in data.items()})
    payload["agent_id"] = agent_id
    title = format_push_title(agent_id, title)

    devices = state.db.list_push_devices(agent_id)
    delivered = 0
    invalid_tokens: list[str] = []
    for device in devices:
        try:
            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=payload,
                token=device["token"],
            )
            messaging.send(message)
            delivered += 1
        except Exception as exc:
            code = getattr(exc, "code", None)
            if str(code) in {"NOT_FOUND", "UNREGISTERED"} or "registration-token-not-registered" in str(exc):
                invalid_tokens.append(device["token"])
            else:
                logger.warning("FCM delivery failed for agent=%s device=%s: %s", agent_id, device["id"], exc)

    if invalid_tokens:
        state.db.delete_push_tokens(agent_id=agent_id, tokens=invalid_tokens)
    return delivered


def push_status(agent_id: str) -> dict[str, Any]:
    configured = is_configured()
    device_count = 0
    if state.db is not None:
        device_count = len(state.db.list_push_devices(agent_id))
    return {
        "configured": configured,
        "device_count": device_count,
        "configuration_error": None if configured else get_configuration_error(),
    }


def _get_messaging():
    global _firebase_initialized
    if state.settings is None:
        return None
    path = (state.settings.vps.fcm_service_account_path or "").strip()
    if not path:
        logger.info("FCM push is not configured: set VELAVPS_FCM_SERVICE_ACCOUNT_PATH in .env.")
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
    except ImportError:
        logger.warning("FCM push is unavailable: firebase-admin is not installed.")
        return None
    if not _firebase_initialized:
        credential_path = Path(path).expanduser()
        config_error = _validate_service_account_file(credential_path)
        if config_error:
            logger.error(config_error)
            return None
        firebase_admin.initialize_app(credentials.Certificate(str(credential_path)))
        _firebase_initialized = True
    return messaging
