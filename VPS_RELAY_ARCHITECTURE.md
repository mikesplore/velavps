# VPS Relay Architecture for the Local Vela Agent

This document describes how the existing local Vela agent should be integrated with a separate VPS-hosted relay service.
It is intended as a handoff document for the next agent or developer who will build the VPS side.

## Goals

- Keep the local agent running on the PC isolated from public access.
- Host a separate VPS relay service that clients can call securely.
- Relay requests from the public VPS to the local agent and return responses back to the client.
- Preserve existing local agent authentication and add a VPS-layer auth boundary.

## Components

### 1. Local Agent (current app)

- Runs on the user’s PC.
- Exposes the existing REST API for system control, media, filesystem, scheduler, maintenance, etc.
- Uses JWT auth for all protected endpoints.
- Should be reachable only by the VPS relay, or through an outbound tunnel created by the local PC.

### 2. VPS Relay Service (new app)

- Runs on a public VPS.
- Exposes a public API for remote clients.
- Authenticates clients separately from the local agent.
- Forwards authorized requests to the local agent.
- Returns the local agent’s responses back to the client.
- May also provide monitoring, logs, and connection management for multiple local agents.

### 3. Remote Client

- Calls the VPS relay service.
- Never calls the local agent directly.
- Uses public endpoint(s) managed by the VPS.

## Recommended Architecture

### Option A: Reverse Tunnel (preferred)

1. The local agent establishes an outbound connection to the VPS.
2. The VPS relay keeps that connection alive.
3. The client sends a request to the VPS relay.
4. The VPS forwards the request through the reverse tunnel to the local agent.
5. The local agent processes the request and returns the response through the same tunnel.

This approach works best when the local PC is behind NAT/firewall.

### Option B: Direct Forwarding (only if reachable)

1. The client calls the VPS relay.
2. The VPS relay forwards the request directly to the local PC’s public address.
3. The local agent responds directly.

Use this only if the local machine is directly reachable from the VPS via a public IP, port forwarding, or VPN.

## Security Model

### Client-to-VPS Authentication

- The VPS relay should authenticate remote clients.
- Use API keys, OAuth, or JWT for client auth.
- Keep client auth separate from local agent auth.

### VPS-to-Local Agent Authentication

- Use the existing local agent JWT or a separate shared secret for the VPS-to-agent channel.
- The local agent should verify the identity of the VPS before executing commands.
- Do not expose local agent endpoints to the public internet without the VPS layer.

### Trust Boundary

- Client auth is handled by the VPS relay.
- Local agent auth is handled by the agent itself.
- The relay acts as a trusted bridge between the two.

## Data Flow

### Request flow

1. Client sends request to VPS relay: `POST /v1/command` or `POST /forward`.
2. VPS validates client auth.
3. VPS determines which local agent should handle the request.
4. VPS forwards the request to the local agent over the tunnel or direct HTTP.
5. Local agent executes the call and returns a response.
6. VPS relays the response back to the client.

### Response flow

- The VPS should preserve HTTP status codes and JSON bodies from the local agent.
- For errors on the VPS itself, return a clear gateway-level error.

## Minimal API contract for the VPS relay

### Registration / heartbeat

- `POST /agents/register` — local agent registers itself with the VPS.
- `POST /agents/heartbeat` — keepalive/presence signal.
- `GET /agents` — list connected agents (admin only).

### Request forwarding

- `POST /agents/{agent_id}/forward` — forward an HTTP request to the given local agent.
- `POST /agents/{agent_id}/commands` — optionally expose simplified commands.

### Health

- `GET /health` — check VPS availability.

## Implementation Notes for the VPS app

### Suggested stack

- FastAPI for the VPS service.
- `httpx` for forwarding requests to the local agent.
- `uvicorn` for deployment.
- `slowapi` or similar for rate limiting.
- A token registry or database if multiple agents are supported.

### Forwarding logic

1. Accept an incoming client request.
2. Reconstruct the target local agent request.
3. Attach a VPS-to-agent authorization header.
4. Send the request to the local agent.
5. Return the local agent response.

### Example forwarding fallback

- If the local agent is not connected, return `503 Service Unavailable`.
- If the local agent returns `401`, the VPS can translate it to `502` or `403`.

## Repo structure recommendation

If you create a separate repo for the VPS relay, use a structure like:

```
/vps-relay/
  README.md
  main.py
  requirements.txt
  config.yaml
  routers/
    agents.py
    forward.py
    auth.py
  services/
    agent_registry.py
    forwarder.py
  tests/
    test_forwarding.py
    test_auth.py
    test_agent_registration.py
```

## What the next agent needs to know

- This repo is the local PC agent.
- The VPS service is a separate app.
- The VPS relay must not rewrite or bypass local agent auth.
- The relay should be a trusted proxy that forwards and returns the same API semantics.
- Prefer a reverse tunnel for reliability when the local PC is behind NAT.

## Summary

Yes — the VPS relay belongs in a separate repo or folder.

This document should make the next agent understand:
- the role of each component,
- the desired flow,
- security boundaries,
- and how to implement the relay cleanly.
