from services.agent_registry import AgentRegistry
from services.forwarder import Forwarder
from services.settings import Settings

registry = AgentRegistry()
settings: Settings | None = None
forwarder: Forwarder | None = None


def initialize_state(settings_obj: Settings) -> None:
    global settings, forwarder
    settings = settings_obj
    forwarder = Forwarder(settings=settings_obj, registry=registry)
