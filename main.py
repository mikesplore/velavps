from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.services.vela_settings import load_settings
from app.services.vela_state import initialize_state

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"

settings = load_settings(CONFIG_PATH)
initialize_state(settings)

from app.routers.vela_relay import router as relay_router

app = FastAPI(title="VPS Relay Service", version="0.1.0")
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.vps.rate_limit])
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

app.include_router(relay_router)


class HealthResponse(BaseModel):
    status: str


class ErrorResponse(BaseModel):
    success: bool = False
    statusCode: int
    message: str
    timestamp: str


def get_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds") + "Z"


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "statusCode": exc.status_code, "message": exc.detail, "timestamp": get_timestamp()},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"success": False, "statusCode": 422, "message": "Validation Error", "timestamp": get_timestamp()},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"success": False, "statusCode": 500, "message": "Internal Server Error", "timestamp": get_timestamp()},
    )


@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok"}
