from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from services.settings import load_settings
from services.state import initialize_state

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"

settings = load_settings(CONFIG_PATH)
initialize_state(settings)

from routers.relay import router as relay_router

app = FastAPI(title="VPS Relay Service", version="0.1.0")
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.vps.rate_limit])
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

app.include_router(relay_router)


class HealthResponse(BaseModel):
    status: str


@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}
