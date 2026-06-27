"""
Test script to verify WebSocket reconnection behavior.
"""
import asyncio
import httpx
import json
from datetime import datetime, timezone, timedelta

# Test 1: Verify database has ws_tokens table
def test_database_schema():
    from app.services.vela_database import VelaDatabase
    import sqlite3
    
    # Initialize database (creates schema)
    db = VelaDatabase("vela.db")
    
    # Now check if table exists
    conn = sqlite3.connect("vela.db")
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ws_tokens'")
    result = cursor.fetchone()
    assert result is not None, "ws_tokens table should exist"
    print("✓ Database schema: ws_tokens table exists")
    conn.close()

# Test 2: Verify token persistence across registry restarts
def test_token_persistence():
    from app.services.vela_database import VelaDatabase
    from app.services.vela_agent_registry import AgentRegistry
    
    db = VelaDatabase("vela_test.db")
    registry = AgentRegistry(db=db)
    
    # Simulate first registration - store a token
    agent_id = "test-agent-reconnect"
    token = "test-token-123"
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    
    # Store token in registry and DB
    asyncio.run(registry.set_agent_ws_token(agent_id, token, expiry))
    
    # Verify token is in memory
    agent = asyncio.run(registry.get_agent(agent_id))
    assert agent is not None, "Agent should be in registry"
    assert agent.ws_token == token, "Token should be in memory"
    
    # Verify token is in database
    token_data = db.get_ws_token(agent_id)
    assert token_data is not None, "Token should be in database"
    assert token_data["token"] == token, "DB token should match"
    
    # Simulate registry restart - create new registry instance with same DB
    registry2 = AgentRegistry(db=db)
    
    # Token should be valid even with fresh registry
    valid = asyncio.run(registry2.validate_agent_ws_token(agent_id, token))
    assert valid, "Token should be valid across registry restarts"
    
    # Clean up
    db.delete_ws_token(agent_id)
    print("✓ Token persistence: Token survives registry restart")
    
    import os
    os.remove("vela_test.db")

# Test 3: Verify token invalidation after expiry
def test_token_expiry():
    from app.services.vela_database import VelaDatabase
    from app.services.vela_agent_registry import AgentRegistry
    
    db = VelaDatabase("vela_test.db")
    registry = AgentRegistry(db=db)
    
    agent_id = "test-agent-expired"
    token = "expired-token-456"
    # Token expired 10 seconds ago
    expiry = datetime.now(timezone.utc) - timedelta(seconds=10)
    
    asyncio.run(registry.set_agent_ws_token(agent_id, token, expiry))
    
    # Token should be invalid
    valid = asyncio.run(registry.validate_agent_ws_token(agent_id, token))
    assert not valid, "Expired token should be invalid"
    
    # Token should be cleaned from DB
    token_data = db.get_ws_token(agent_id)
    assert token_data is None, "Expired token should be deleted from DB"
    
    # Clean up
    db.delete_ws_token(agent_id)
    print("✓ Token expiry: Expired tokens are rejected and cleaned up")
    
    import os
    os.remove("vela_test.db")

# Test 4: Verify reissue endpoint works
def test_reissue_flow():
    from app.services.vela_database import VelaDatabase
    from app.services.vela_agent_registry import AgentRegistry
    
    db = VelaDatabase("vela_test.db")
    registry = AgentRegistry(db=db)
    
    # Manually create an agent (simulate /register)
    agent_id = "test-agent-reissue"
    secret = "test-secret-789"
    db.create_secret(secret)
    agent = db.register_agent(agent_id, secret, None, None)
    
    # Simulate disconnect - clear in-memory connection but keep DB state
    asyncio.run(registry.remove_websocket_connection(agent_id))
    
    # Agent should still exist in DB
    db_agent = db.get_agent_by_id(agent_id)
    assert db_agent is not None, "Agent should still exist in DB after disconnect"
    assert db_agent.agent_id == agent_id
    
    # Now reissue a token (simulating POST /agents/{agent_id}/ws-token)
    new_token = "reissued-token-999"
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    asyncio.run(registry.set_agent_ws_token(agent_id, new_token, expiry))
    
    # Token should be in DB and valid
    token_data = db.get_ws_token(agent_id)
    assert token_data is not None, "New token should be in DB"
    assert token_data["token"] == new_token
    
    # Validation should work
    valid = asyncio.run(registry.validate_agent_ws_token(agent_id, new_token))
    assert valid, "Reissued token should be valid"
    
    # Clean up
    db.delete_ws_token(agent_id)
    print("✓ Reissue flow: Agent can reconnect using /agents/{id}/ws-token")
    
    import os
    os.remove("vela_test.db")

if __name__ == "__main__":
    print("Testing WebSocket reconnection mechanisms...\n")
    test_database_schema()
    test_token_persistence()
    test_token_expiry()
    test_reissue_flow()
    print("\n✅ All reconnection tests passed!")