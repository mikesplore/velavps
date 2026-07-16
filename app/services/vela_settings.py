"""
Settings loader – single source of truth.

All config is loaded from config.yaml.
"""
from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel


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
    # Admin API keys for management endpoints (optional)
    api_keys: List[str] = []


class Settings(BaseModel):
    vps: VPSSettings

    @classmethod
    def load(cls, path: Path) -> "Settings":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        vps_data: dict = data.get("vps", {})

        return cls(vps=VPSSettings(**vps_data))


settings: "Settings | None" = None


def load_settings(path: Path) -> Settings | None:
    global settings
    settings = Settings.load(path)
    return settings