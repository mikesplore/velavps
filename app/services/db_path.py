from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DB_PATH_FALLBACK = "/app/data/vela.db"
LOCAL_DB_PATH_FALLBACK = str((Path(__file__).resolve().parents[2] / ".velavps" / "vela.db").resolve())


def get_db_path() -> str:
    """
    Centralized SQLite path configuration.

    Preferred: DB_PATH
    Back-compat: VELAVPS_DB_PATH
    Fallback: /app/data/vela.db (container volume mount)
    """
    return (
        os.getenv("DB_PATH", "").strip()
        or os.getenv("VELAVPS_DB_PATH", "").strip()
        or (DEFAULT_DB_PATH_FALLBACK if Path("/app").is_dir() else LOCAL_DB_PATH_FALLBACK)
    )
