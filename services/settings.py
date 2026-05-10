from pathlib import Path

import yaml
from pydantic import BaseModel
from typing import List


class VPSSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    api_keys: List[str] = []
    agent_shared_secret: str = ""
    allow_direct_agent_forwarding: bool = True
    default_agent_timeout_seconds: int = 20
    rate_limit: str = "100/minute"


class Settings(BaseModel):
    vps: VPSSettings

    @classmethod
    def load(cls, path: Path) -> "Settings":
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        return cls(**data)


settings: Settings | None = None


def load_settings(path: Path) -> Settings:
    global settings
    settings = Settings.load(path)
    return settings
