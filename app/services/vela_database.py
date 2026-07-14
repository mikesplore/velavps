"""SQLite persistence and onboarding lifecycle primitives."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

STATUS_INITIATED = "INITIATED"
STATUS_AWAITING_PAIR = "AWAITING_PAIR"
STATUS_PAIRED = "PAIRED"
STATUS_ACTIVE = "ACTIVE"
STATUS_EXPIRED = "EXPIRED"
STATUS_REVOKED = "REVOKED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return _utcnow()
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class AgentRecord:
    agent_id: str
    secret: Optional[str]
    tenant_id: Optional[str]
    display_name: Optional[str]
    public_address: Optional[str]
    metadata: Dict[str, Any]
    status: str
    last_seen: datetime
    created_at: datetime

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "display_name": self.display_name,
            "public_address": self.public_address,
            "metadata": self.metadata,
            "status": self.status,
            "last_seen_at": self.last_seen.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


class VelaDatabase:
    def __init__(self, db_path: str = "vela.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
        return self._local.connection

    @contextmanager
    def transaction(self):
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _column_exists(self, table: str, column: str) -> bool:
        conn = self._get_connection()
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def _init_db(self):
        conn = self._get_connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS secrets (
                secret TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT NOT NULL,
                secret TEXT REFERENCES secrets(secret) ON DELETE CASCADE,
                public_address TEXT,
                metadata TEXT,
                status TEXT DEFAULT 'inactive',
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (secret, agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_agents_agent_id ON agents(agent_id);
            CREATE INDEX IF NOT EXISTS idx_agents_secret ON agents(secret);
            """
        )
        if not self._column_exists("agents", "tenant_id"):
            conn.execute("ALTER TABLE agents ADD COLUMN tenant_id TEXT")
        if not self._column_exists("agents", "display_name"):
            conn.execute("ALTER TABLE agents ADD COLUMN display_name TEXT")

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ws_tokens (
                agent_id TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                expiry TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_ws_tokens_expiry ON ws_tokens(expiry);

            CREATE TABLE IF NOT EXISTS agent_pairing_sessions (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                pairing_code_hash TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                paired_user_secret TEXT,
                activation_token_hash TEXT,
                activation_expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_pairing_agent ON agent_pairing_sessions(agent_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_pairing_hash ON agent_pairing_sessions(pairing_code_hash);
            CREATE INDEX IF NOT EXISTS idx_pairing_status ON agent_pairing_sessions(status, expires_at);

            CREATE TABLE IF NOT EXISTS agent_credentials (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                credential_hash TEXT NOT NULL,
                scopes TEXT NOT NULL,
                issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_agent_credentials_agent ON agent_credentials(agent_id, revoked_at);

            CREATE TABLE IF NOT EXISTS app_agent_links (
                user_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, agent_id)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                agent_id TEXT,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()

    # Secret management
    def secret_exists(self, secret: str) -> bool:
        conn = self._get_connection()
        return conn.execute("SELECT 1 FROM secrets WHERE secret = ?", (secret,)).fetchone() is not None

    def create_secret(self, secret: str) -> bool:
        try:
            with self.transaction() as conn:
                conn.execute("INSERT OR IGNORE INTO secrets (secret) VALUES (?)", (secret,))
            return True
        except sqlite3.IntegrityError:
            return False

    def validate_secret(self, secret: str) -> bool:
        return self.secret_exists(secret)

    def record_audit_event(self, event_type: str, agent_id: Optional[str], payload: Optional[Dict[str, Any]] = None) -> None:
        with self.transaction() as conn:
            self._record_audit_event_conn(conn, event_type, agent_id, payload)

    def _record_audit_event_conn(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        agent_id: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        conn.execute(
            "INSERT INTO audit_events (id, event_type, agent_id, payload) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), event_type, agent_id, json.dumps(payload or {})),
        )

    # Legacy agent APIs
    def register_agent(
        self,
        agent_id: str,
        secret: str,
        public_address: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentRecord:
        if not self.secret_exists(secret):
            raise ValueError(f"Invalid secret: {secret}")
        metadata_json = json.dumps(metadata or {})

        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT secret FROM agents WHERE agent_id = ? AND secret != ?",
                (agent_id, secret),
            ).fetchone()
            if existing:
                raise ConflictError(f"Agent ID '{agent_id}' is already registered to another user")

            existing_agent = conn.execute(
                "SELECT 1 FROM agents WHERE agent_id = ? AND secret = ?",
                (agent_id, secret),
            ).fetchone()
            if existing_agent:
                conn.execute(
                    """
                    UPDATE agents SET
                        public_address = COALESCE(?, public_address),
                        metadata = COALESCE(?, metadata),
                        status = ?,
                        last_seen = CURRENT_TIMESTAMP
                    WHERE agent_id = ? AND secret = ?
                    """,
                    (public_address, metadata_json, STATUS_ACTIVE, agent_id, secret),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO agents (agent_id, secret, public_address, metadata, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (agent_id, secret, public_address, metadata_json, STATUS_ACTIVE),
                )
            return self._get_agent(conn, agent_id, secret)

    def _agent_from_row(self, row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"],
            secret=row["secret"],
            tenant_id=row["tenant_id"] if "tenant_id" in row.keys() else None,
            display_name=row["display_name"] if "display_name" in row.keys() else None,
            public_address=row["public_address"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            status=row["status"],
            last_seen=_parse_dt(row["last_seen"]),
            created_at=_parse_dt(row["created_at"]),
        )

    def _get_agent(self, conn: sqlite3.Connection, agent_id: str, secret: str) -> AgentRecord:
        row = conn.execute("SELECT * FROM agents WHERE agent_id = ? AND secret = ?", (agent_id, secret)).fetchone()
        if not row:
            raise ValueError(f"Agent not found: {agent_id}")
        return self._agent_from_row(row)

    def get_agent(self, agent_id: str, secret: str) -> Optional[AgentRecord]:
        conn = self._get_connection()
        try:
            return self._get_agent(conn, agent_id, secret)
        except ValueError:
            return None

    def get_agent_by_id(self, agent_id: str) -> Optional[AgentRecord]:
        conn = self._get_connection()
        row = conn.execute("SELECT * FROM agents WHERE agent_id = ? ORDER BY created_at DESC LIMIT 1", (agent_id,)).fetchone()
        return self._agent_from_row(row) if row else None

    def list_agents(self, secret: str) -> List[AgentRecord]:
        conn = self._get_connection()
        return [self._agent_from_row(row) for row in conn.execute("SELECT * FROM agents WHERE secret = ?", (secret,))]

    def heartbeat_agent(self, agent_id: str, secret: str) -> Optional[AgentRecord]:
        conn = self._get_connection()
        conn.execute(
            "UPDATE agents SET last_seen = CURRENT_TIMESTAMP, status = ? WHERE agent_id = ? AND secret = ?",
            (STATUS_ACTIVE, agent_id, secret),
        )
        return self.get_agent(agent_id, secret)

    def remove_agent(self, agent_id: str, secret: str) -> bool:
        conn = self._get_connection()
        result = conn.execute("DELETE FROM agents WHERE agent_id = ? AND secret = ?", (agent_id, secret))
        return result.rowcount > 0

    def mark_agent_inactive(self, agent_id: str, secret: str):
        conn = self._get_connection()
        conn.execute("UPDATE agents SET status = 'inactive' WHERE agent_id = ? AND secret = ?", (agent_id, secret))

    # Pairing flow
    def create_or_refresh_pairing_session(
        self,
        agent_name: str,
        device_info: Optional[Dict[str, Any]],
        tenant_hint: Optional[str],
        pairing_ttl_seconds: int,
        existing_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        agent_id = existing_agent_id or f"agt_{uuid.uuid4().hex[:16]}"
        now = _utcnow()
        expires_at = now + timedelta(seconds=pairing_ttl_seconds)
        pairing_code = f"{secrets.randbelow(10**8):08d}"
        pairing_code_hash = _hash_token(pairing_code)

        with self.transaction() as conn:
            existing_agent = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ? ORDER BY created_at DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
            if existing_agent:
                conn.execute(
                    """
                    UPDATE agents SET
                        display_name = COALESCE(?, display_name),
                        tenant_id = COALESCE(?, tenant_id),
                        metadata = COALESCE(?, metadata),
                        status = ?,
                        last_seen = CURRENT_TIMESTAMP
                    WHERE agent_id = ?
                    """,
                    (agent_name, tenant_hint, json.dumps(device_info or {}), STATUS_AWAITING_PAIR, agent_id),
                )
            else:
                pending_secret = f"pending:{agent_id}"
                self.create_secret(pending_secret)
                conn.execute(
                    """
                    INSERT INTO agents (
                        agent_id, secret, tenant_id, display_name, metadata, status, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (agent_id, pending_secret, tenant_hint, agent_name, json.dumps(device_info or {}), STATUS_AWAITING_PAIR),
                )

            conn.execute(
                """
                UPDATE agent_pairing_sessions
                SET status = ?, expires_at = CURRENT_TIMESTAMP
                WHERE agent_id = ? AND status IN (?, ?)
                """,
                (STATUS_EXPIRED, agent_id, STATUS_AWAITING_PAIR, STATUS_INITIATED),
            )

            session_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO agent_pairing_sessions
                (id, agent_id, pairing_code_hash, expires_at, status, attempt_count)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (session_id, agent_id, pairing_code_hash, expires_at.isoformat(), STATUS_AWAITING_PAIR),
            )

        self.record_audit_event("agent_register_started", agent_id, {"session_id": session_id})
        return {
            "agent_id": agent_id,
            "pairing_code": pairing_code,
            "pairing_expires_in": pairing_ttl_seconds,
            "session_id": session_id,
        }

    def _mark_expired_sessions(self, conn: sqlite3.Connection, agent_id: str) -> None:
        conn.execute(
            """
            UPDATE agent_pairing_sessions
            SET status = ?
            WHERE agent_id = ? AND status IN (?, ?) AND expires_at <= CURRENT_TIMESTAMP
            """,
            (STATUS_EXPIRED, agent_id, STATUS_AWAITING_PAIR, STATUS_INITIATED),
        )

    def get_registration_status(self, agent_id: str, activation_ttl_seconds: int) -> Dict[str, Any]:
        with self.transaction() as conn:
            self._mark_expired_sessions(conn, agent_id)
            agent = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ? ORDER BY created_at DESC LIMIT 1",
                (agent_id,),
            ).fetchone()
            if not agent:
                raise ValueError("Agent not found")

            status_value = agent["status"]
            session = conn.execute(
                """
                SELECT * FROM agent_pairing_sessions
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            response: Dict[str, Any] = {"status": status_value}
            if status_value == STATUS_PAIRED and session:
                activation_token = secrets.token_urlsafe(24)
                activation_hash = _hash_token(activation_token)
                activation_expires_at = (_utcnow() + timedelta(seconds=activation_ttl_seconds)).isoformat()
                conn.execute(
                    """
                    UPDATE agent_pairing_sessions
                    SET activation_token_hash = ?, activation_expires_at = ?
                    WHERE id = ?
                    """,
                    (activation_hash, activation_expires_at, session["id"]),
                )
                response["activation_token"] = activation_token
            return response

    def complete_pairing(self, pairing_code: str, user_secret: str, agent_label: Optional[str] = None) -> Dict[str, Any]:
        code_hash = _hash_token(pairing_code)
        with self.transaction() as conn:
            session = conn.execute(
                "SELECT * FROM agent_pairing_sessions WHERE pairing_code_hash = ? ORDER BY created_at DESC LIMIT 1",
                (code_hash,),
            ).fetchone()
            if not session:
                raise ValueError("invalid_or_expired_code")

            if session["status"] == STATUS_PAIRED and session["paired_user_secret"] == user_secret:
                return {"agent_id": session["agent_id"], "status": STATUS_PAIRED, "idempotent": True}

            if session["status"] not in {STATUS_AWAITING_PAIR, STATUS_INITIATED}:
                raise ValueError("invalid_or_expired_code")

            if _parse_dt(session["expires_at"]) <= _utcnow():
                conn.execute(
                    "UPDATE agent_pairing_sessions SET status = ?, attempt_count = attempt_count + 1 WHERE id = ?",
                    (STATUS_EXPIRED, session["id"]),
                )
                conn.execute(
                    "UPDATE agents SET status = ? WHERE agent_id = ?",
                    (STATUS_EXPIRED, session["agent_id"]),
                )
                self._record_audit_event_conn(conn, "pairing_expired", session["agent_id"], {"session_id": session["id"]})
                raise ValueError("invalid_or_expired_code")

            now = _utcnow().isoformat()
            conn.execute(
                """
                UPDATE agent_pairing_sessions
                SET status = ?, used_at = ?, paired_user_secret = ?
                WHERE id = ? AND used_at IS NULL
                """,
                (STATUS_PAIRED, now, user_secret, session["id"]),
            )
            updated = conn.execute("SELECT used_at FROM agent_pairing_sessions WHERE id = ?", (session["id"],)).fetchone()
            if not updated or not updated["used_at"]:
                raise ValueError("invalid_or_expired_code")

            self.create_secret(user_secret)
            conn.execute(
                """
                UPDATE agents
                SET secret = ?, status = ?, tenant_id = COALESCE(tenant_id, ?), display_name = COALESCE(?, display_name)
                WHERE agent_id = ?
                """,
                (user_secret, STATUS_PAIRED, user_secret, agent_label, session["agent_id"]),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO app_agent_links (user_id, agent_id)
                VALUES (?, ?)
                """,
                (user_secret, session["agent_id"]),
            )
            self._record_audit_event_conn(conn, "pairing_completed", session["agent_id"], {"session_id": session["id"]})
            return {"agent_id": session["agent_id"], "status": STATUS_PAIRED, "idempotent": False}

    def activate_agent(self, agent_id: str, activation_token: str, ttl_seconds: int) -> Dict[str, Any]:
        token_hash = _hash_token(activation_token)
        with self.transaction() as conn:
            session = conn.execute(
                """
                SELECT * FROM agent_pairing_sessions
                WHERE agent_id = ? AND status = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (agent_id, STATUS_PAIRED),
            ).fetchone()
            if not session:
                raise ValueError("invalid_activation_token")

            if not session["activation_token_hash"] or session["activation_token_hash"] != token_hash:
                raise ValueError("invalid_activation_token")
            if not session["activation_expires_at"] or _parse_dt(session["activation_expires_at"]) <= _utcnow():
                raise ValueError("invalid_activation_token")

            credential = secrets.token_urlsafe(32)
            credential_hash = _hash_token(credential)
            credential_id = str(uuid.uuid4())
            scopes = ["agent:relay", "agent:heartbeat", "agent:ws"]
            conn.execute(
                """
                INSERT INTO agent_credentials (id, agent_id, credential_hash, scopes)
                VALUES (?, ?, ?, ?)
                """,
                (credential_id, agent_id, credential_hash, json.dumps(scopes)),
            )
            conn.execute(
                "UPDATE agents SET status = ?, last_seen = CURRENT_TIMESTAMP WHERE agent_id = ?",
                (STATUS_ACTIVE, agent_id),
            )
            conn.execute(
                """
                UPDATE agent_pairing_sessions
                SET activation_token_hash = NULL, activation_expires_at = NULL
                WHERE id = ?
                """,
                (session["id"],),
            )
            self._record_audit_event_conn(conn, "agent_activated", agent_id, {"credential_id": credential_id})
            return {"credential": credential, "expires_in": ttl_seconds, "scopes": scopes}

    def revoke_agent_credentials(self, agent_id: str, revoked_by: Optional[str] = None) -> int:
        with self.transaction() as conn:
            result = conn.execute(
                """
                UPDATE agent_credentials
                SET revoked_at = CURRENT_TIMESTAMP
                WHERE agent_id = ? AND revoked_at IS NULL
                """,
                (agent_id,),
            )
            conn.execute("UPDATE agents SET status = ? WHERE agent_id = ?", (STATUS_REVOKED, agent_id))
            self._record_audit_event_conn(conn, "credential_revoked", agent_id, {"revoked_by": revoked_by})
            return result.rowcount

    # WebSocket token persistence
    def store_ws_token(self, agent_id: str, token: str, expiry: datetime) -> bool:
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO ws_tokens (agent_id, token, expiry)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                token = excluded.token,
                expiry = excluded.expiry,
                created_at = CURRENT_TIMESTAMP
            """,
            (agent_id, token, expiry.isoformat()),
        )
        return True

    def get_ws_token(self, agent_id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        row = conn.execute("SELECT token, expiry FROM ws_tokens WHERE agent_id = ?", (agent_id,)).fetchone()
        if not row:
            return None
        return {"token": row["token"], "expiry": _parse_dt(row["expiry"])}

    def delete_ws_token(self, agent_id: str) -> bool:
        conn = self._get_connection()
        result = conn.execute("DELETE FROM ws_tokens WHERE agent_id = ?", (agent_id,))
        return result.rowcount > 0

    def cleanup_expired_ws_tokens(self) -> int:
        conn = self._get_connection()
        result = conn.execute("DELETE FROM ws_tokens WHERE expiry <= CURRENT_TIMESTAMP")
        return result.rowcount


class ConflictError(Exception):
    pass
