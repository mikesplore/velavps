"""
Settings loader – config.yaml plus optional .env overrides for secrets.
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
    # Firebase Admin service account JSON (matches the official Android APK project)
    fcm_service_account_path: str = ""
    # Wait this long after tunnel disconnect before notifying phones (avoids flap noise)
    connectivity_offline_delay_seconds: int = 30
    # Admin API keys for management endpoints (optional)
    api_keys: List[str] = []

    @classmethod
    def from_yaml(cls, data: dict) -> "VPSSettings":
        settings = cls(**data)
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
