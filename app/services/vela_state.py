from .vela_agent_registry import AgentRegistry
from .vela_database import VelaDatabase
from .vela_forwarder import Forwarder
from .vela_settings import Settings

registry = AgentRegistry()
settings: Settings | None = None
forwarder: Forwarder | None = None
db: VelaDatabase | None = None


def initialize_state(settings_obj: Settings) -> None:
    global settings, forwarder, db
    settings = settings_obj
    db = VelaDatabase()  # Initialize database
    registry._db = db  # Give registry access to DB for token persistence
    forwarder = Forwarder(settings=settings_obj, registry=registry, db=db)
