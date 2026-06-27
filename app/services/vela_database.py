"""
SQLite database layer for Vela multi-tenant relay.

Provides persistence for:
- User secrets (secret-as-identity model)
- Agent registrations (secret -> agent_id mapping)
"""
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AgentRecord:
    """Represents a registered agent in the database."""
    agent_id: str
    secret: str
    public_address: Optional[str]
    metadata: Dict[str, Any]
    status: str
    last_seen: datetime
    created_at: datetime

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "public_address": self.public_address,
            "metadata": self.metadata,
            "status": self.status,
            "last_seen": self.last_seen.isoformat() + "Z",
            "created_at": self.created_at.isoformat() + "Z",
        }


class VelaDatabase:
    """SQLite database manager for Vela relay."""

    def __init__(self, db_path: str = "vela.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.execute("PRAGMA synchronous=NORMAL")
        return self._local.connection

    @contextmanager
    def transaction(self):
        """Context manager for database transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e

    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        conn.executescript("""
            -- Secrets table: stores unique secrets (secret-as-identity)
            CREATE TABLE IF NOT EXISTS secrets (
                secret TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Agents table: maps secrets to agent registrations
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT NOT NULL,
                secret TEXT NOT NULL REFERENCES secrets(secret) ON DELETE CASCADE,
                public_address TEXT,
                metadata TEXT,
                status TEXT DEFAULT 'inactive',
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (secret, agent_id)
            );

            -- Index for looking up agents by agent_id (for collision detection)
            CREATE INDEX IF NOT EXISTS idx_agents_agent_id ON agents(agent_id);

            -- Index for listing agents by secret
            CREATE INDEX IF NOT EXISTS idx_agents_secret ON agents(secret);

            -- WebSocket tokens table: persistent token storage for reconnection
            CREATE TABLE IF NOT EXISTS ws_tokens (
                agent_id TEXT PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
                token TEXT NOT NULL,
                expiry TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Index for cleaning expired tokens
            CREATE INDEX IF NOT EXISTS idx_ws_tokens_expiry ON ws_tokens(expiry);
        """)
        conn.commit()

    # ─── Secret Management ───

    def secret_exists(self, secret: str) -> bool:
        """Check if a secret exists."""
        conn = self._get_connection()
        cursor = conn.execute("SELECT 1 FROM secrets WHERE secret = ?", (secret,))
        return cursor.fetchone() is not None

    def create_secret(self, secret: str) -> bool:
        """
        Create a new secret.
        Returns True if created, False if already exists.
        """
        try:
            with self.transaction() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO secrets (secret) VALUES (?)",
                    (secret,)
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def validate_secret(self, secret: str) -> bool:
        """Validate that a secret exists."""
        return self.secret_exists(secret)

    # ─── Agent Registration ───

    def register_agent(
        self,
        agent_id: str,
        secret: str,
        public_address: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentRecord:
        """
        Register or update an agent.

        Returns:
            AgentRecord: The registered/updated agent

        Raises:
            ValueError: If the secret is invalid
            ConflictError: If agent_id is taken by a different secret
        """
        import json

        # Validate secret exists
        if not self.secret_exists(secret):
            raise ValueError(f"Invalid secret: {secret}")

        metadata_json = json.dumps(metadata or {})

        with self.transaction() as conn:
            # Check for collision: agent_id taken by different secret
            existing = conn.execute(
                """
                SELECT secret FROM agents 
                WHERE agent_id = ? AND secret != ?
                """,
                (agent_id, secret)
            ).fetchone()

            if existing:
                raise ConflictError(
                    f"Agent ID '{agent_id}' is already registered to another user"
                )

            # Check if this agent already exists with this secret
            existing_agent = conn.execute(
                """
                SELECT * FROM agents 
                WHERE agent_id = ? AND secret = ?
                """,
                (agent_id, secret)
            ).fetchone()

            if existing_agent:
                # Update existing agent
                conn.execute(
                    """
                    UPDATE agents SET
                        public_address = COALESCE(?, public_address),
                        metadata = COALESCE(?, metadata),
                        status = 'active',
                        last_seen = CURRENT_TIMESTAMP
                    WHERE agent_id = ? AND secret = ?
                    """,
                    (public_address, metadata_json, agent_id, secret)
                )
            else:
                # Insert new agent
                conn.execute(
                    """
                    INSERT INTO agents (agent_id, secret, public_address, metadata, status)
                    VALUES (?, ?, ?, ?, 'active')
                    """,
                    (agent_id, secret, public_address, metadata_json)
                )

            # Fetch and return the agent
            return self._get_agent(conn, agent_id, secret)

    def _get_agent(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        secret: str,
    ) -> AgentRecord:
        """Fetch an agent by agent_id and secret."""
        import json

        row = conn.execute(
            """
            SELECT agent_id, secret, public_address, metadata, status, last_seen, created_at
            FROM agents
            WHERE agent_id = ? AND secret = ?
            """,
            (agent_id, secret)
        ).fetchone()

        if not row:
            raise ValueError(f"Agent not found: {agent_id}")

        return AgentRecord(
            agent_id=row["agent_id"],
            secret=row["secret"],
            public_address=row["public_address"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            status=row["status"],
            last_seen=datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00")) if row["last_seen"] else datetime.now(timezone.utc),
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) if row["created_at"] else datetime.now(timezone.utc),
        )

    def get_agent(self, agent_id: str, secret: str) -> Optional[AgentRecord]:
        """Get an agent by agent_id and secret."""
        conn = self._get_connection()
        try:
            return self._get_agent(conn, agent_id, secret)
        except ValueError:
            return None

    def get_agent_by_id(self, agent_id: str) -> Optional[AgentRecord]:
        """Get an agent by agent_id only (for relay lookups)."""
        import json

        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()

        if not row:
            return None

        return AgentRecord(
            agent_id=row["agent_id"],
            secret=row["secret"],
            public_address=row["public_address"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            status=row["status"],
            last_seen=datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00")) if row["last_seen"] else datetime.now(timezone.utc),
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) if row["created_at"] else datetime.now(timezone.utc),
        )

    def list_agents(self, secret: str) -> List[AgentRecord]:
        """List all agents for a given secret (user)."""
        import json

        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM agents WHERE secret = ?",
            (secret,)
        )

        agents = []
        for row in cursor:
            agents.append(AgentRecord(
                agent_id=row["agent_id"],
                secret=row["secret"],
                public_address=row["public_address"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                status=row["status"],
                last_seen=datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00")) if row["last_seen"] else datetime.now(timezone.utc),
                created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")) if row["created_at"] else datetime.now(timezone.utc),
            ))
        return agents

    def heartbeat_agent(self, agent_id: str, secret: str) -> Optional[AgentRecord]:
        """Update agent's last_seen timestamp."""
        conn = self._get_connection()
        conn.execute(
            """
            UPDATE agents SET
                last_seen = CURRENT_TIMESTAMP,
                status = 'active'
            WHERE agent_id = ? AND secret = ?
            """,
            (agent_id, secret)
        )
        return self.get_agent(agent_id, secret)

    def remove_agent(self, agent_id: str, secret: str) -> bool:
        """Remove an agent registration."""
        conn = self._get_connection()
        result = conn.execute(
            "DELETE FROM agents WHERE agent_id = ? AND secret = ?",
            (agent_id, secret)
        )
        return result.rowcount > 0

    def mark_agent_inactive(self, agent_id: str, secret: str):
        """Mark an agent as inactive (disconnected)."""
        conn = self._get_connection()
        conn.execute(
            """
            UPDATE agents SET status = 'inactive'
            WHERE agent_id = ? AND secret = ?
            """,
            (agent_id, secret)
        )

    # ─── WebSocket Token Persistence ───

    def store_ws_token(self, agent_id: str, token: str, expiry: datetime) -> bool:
        """Store or update a WebSocket token for reconnection."""
        expiry_str = expiry.isoformat()
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
            (agent_id, token, expiry_str)
        )
        return True

    def get_ws_token(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get the current WebSocket token for an agent."""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT token, expiry FROM ws_tokens WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()
        if not row:
            return None
        expiry_str = row["expiry"]
        expiry = datetime.fromisoformat(expiry_str) if expiry_str else datetime.now(timezone.utc)
        return {"token": row["token"], "expiry": expiry}

    def delete_ws_token(self, agent_id: str) -> bool:
        """Delete a WebSocket token after use or on disconnect."""
        conn = self._get_connection()
        result = conn.execute(
            "DELETE FROM ws_tokens WHERE agent_id = ?",
            (agent_id,)
        )
        return result.rowcount > 0

    def cleanup_expired_ws_tokens(self) -> int:
        """Remove expired tokens. Returns count of deleted rows."""
        conn = self._get_connection()
        result = conn.execute(
            "DELETE FROM ws_tokens WHERE expiry <= CURRENT_TIMESTAMP"
        )
        return result.rowcount


class ConflictError(Exception):
    """Raised when a resource conflict occurs (HTTP 409)."""
    pass
