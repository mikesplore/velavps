"""
Settings loader – single source of truth.

Non-secret config  →  config.yaml
Secrets            →  environment variables (loaded from .env by start.sh or dotenv)

Required env vars:
  VPS_API_KEYS        comma-separated list of admin API keys (for management endpoints)

Note: Agent secrets are now per-user and stored in the database (secret-as-identity model).
      Each user generates their own secret that serves as both agent registration secret
      and client API key.
"""
import os
from pathlib import Path
from typing import List

import yaml
from pydantic import BaseModel
from dotenv import load_dotenv

# Load .env file from the project root (or current working directory)
# override=True ensures that if an env var is already set in the shell,
# it takes precedence over the .env file, which is usually desired for production.
load_dotenv(override=False)


class VPSSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    allow_direct_agent_forwarding: bool = True
    default_agent_timeout_seconds: int = 20
    rate_limit: str = "100/minute"
    legacy_registration_enabled: bool = True
    pairing_code_ttl_seconds: int = 600
    activation_token_ttl_seconds: int = 180
    # Admin API keys – for management endpoints (optional)
    api_keys: List[str] = []


class Settings(BaseModel):
    vps: VPSSettings

    @classmethod
    def load(cls, path: Path) -> "Settings":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

        vps_data: dict = data.get("vps", {})

        # Admin API keys from env vars (optional - for management endpoints)
        raw_keys = os.environ.get("VPS_API_KEYS", "").strip()
        api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

        vps_data["api_keys"] = api_keys

        return cls(vps=VPSSettings(**vps_data))


settings: "Settings | None" = None


def load_settings(path: Path) -> Settings | None:
    global settings
    settings = Settings.load(path)
    return settings