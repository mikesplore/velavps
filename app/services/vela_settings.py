"""
Settings loader – config.yaml plus optional .env overrides for secrets.

Env-var overrides (see from_yaml) make this container-friendly: any VPS_* /
VELAVPS_* setting can be supplied at deploy time without rebuilding the image
or editing config.yaml. When an env var is absent, the config.yaml / pydantic
default is used, so local dev behaviour is unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


class VPSSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    allow_direct_agent_forwarding: bool = True
    default_agent_timeout_seconds: int = 20
    rate_limit: str = "100/minute"
    legacy_registration_enabled: bool = True
    pairing_code_ttl_seconds: int = 600
    activation_token_ttl_seconds: int = 180
    agent_connect_wait_seconds: int = 8
    max_agent_waiters: int = 32
    # Firebase Admin service account JSON (matches the official Android APK project)
    fcm_service_account_path: str = ""
    # Wait this long after tunnel disconnect before notifying phones (avoids flap noise)
    connectivity_offline_delay_seconds: int = 30
    # Admin API keys for management endpoints (optional)
    api_keys: List[str] = []

    @classmethod
    def from_yaml(cls, data: dict) -> "VPSSettings":
        settings = cls(**data)

        # Env-var overrides (container / .env friendly). Each is applied only
        # when set, so the config.yaml defaults remain intact for local dev.
        if (v := os.getenv("VPS_HOST", "").strip()):
            settings.host = v
        if (p := os.getenv("VPS_PORT", "").strip()):
            settings.port = int(p)
        if (v := os.getenv("VPS_RATE_LIMIT", "").strip()):
            settings.rate_limit = v
        if (v := os.getenv("VPS_ALLOW_DIRECT_AGENT_FORWARDING", "").strip()):
            settings.allow_direct_agent_forwarding = v.lower() in {"1", "true", "yes", "on"}
        if (v := os.getenv("VPS_LEGACY_REGISTRATION_ENABLED", "").strip()):
            settings.legacy_registration_enabled = v.lower() in {"1", "true", "yes", "on"}
        if (v := os.getenv("VPS_DEFAULT_AGENT_TIMEOUT_SECONDS", "").strip()):
            settings.default_agent_timeout_seconds = int(v)
        if (v := os.getenv("VPS_AGENT_CONNECT_WAIT_SECONDS", "").strip()):
            settings.agent_connect_wait_seconds = int(v)
        if (v := os.getenv("VPS_PAIRING_CODE_TTL_SECONDS", "").strip()):
            settings.pairing_code_ttl_seconds = int(v)
        if (v := os.getenv("VPS_ACTIVATION_TOKEN_TTL_SECONDS", "").strip()):
            settings.activation_token_ttl_seconds = int(v)
        if (v := os.getenv("VPS_CONNECTIVITY_OFFLINE_DELAY_SECONDS", "").strip()):
            settings.connectivity_offline_delay_seconds = int(v)
        if (v := os.getenv("VPS_API_KEYS", "").strip()):
            settings.api_keys = [k.strip() for k in v.split(",") if k.strip()]

        # Firebase Admin service-account JSON path
        env_fcm = (
            os.getenv("VELAVPS_FCM_SERVICE_ACCOUNT_PATH", "").strip()
            or os.getenv("FCM_SERVICE_ACCOUNT_PATH", "").strip()
        )
        if env_fcm:
            settings.fcm_service_account_path = env_fcm
        return settings


class Settings(BaseModel):
    vps: VPSSettings

    @classmethod
    def load(cls, path: Path) -> "Settings":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        vps_data: dict = data.get("vps", {})
        return cls(vps=VPSSettings.from_yaml(vps_data))


settings: "Settings | None" = None


def load_settings(path: Path) -> Settings | None:
    global settings
    settings = Settings.load(path)
    return settings
