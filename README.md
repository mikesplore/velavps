# VPS Relay Service

This repository implements a VPS relay service for the local Vela agent.
It is designed to accept public client requests, authenticate them, and forward authorized calls to one or more registered local agents.

## Features

- Pairing-based onboarding (code + PIN) with no pre-login requirement
- One-time relay secret sharing to Android and agent
- Reverse-tunnel forwarding via WebSocket when agents are connected
- Direct HTTP forwarding fallback when local agent public address is available
- Preservation of local agent response status and body
- Legacy `/register` flow behind feature flag

## Quick start

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Start the app:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Alternative: use the included script to create a virtual environment, install dependencies, and launch the app:

```bash
./start.sh
```

3. Use `GET /health` to verify availability.

## Onboarding flow (pairing-based)

The new onboarding UX uses short-lived pairing material to connect Android + agent, then returns a long-lived shared `relay_secret` used for authenticated relay calls.

### 1) Agent starts pairing

`POST /agents/register/start`

Request:

```json
{
  "agent_name": "android-test-agent",
  "device_info": {"device_fingerprint": "pixel8-fp-123"},
  "tenant_hint": "tenant-a"
}
```

Response (example):

```json
{
  "api_version": "2026-07-pairing-v1",
  "agent_id": "agt_f39c14968e14497d",
  "pairing_code": "13487869",
  "pairing_pin": "710283",
  "pairing_expires_in": 600,
  "pairing_qr_payload": "vela://pair?code=13487869&agent_id=agt_f39c14968e14497d"
}
```

### 2) Android completes pairing (no auth header)

`POST /pair/complete`

Request:

```json
{
  "pairing_code": "13487869",
  "pairing_pin": "710283",
  "agent_label": "Android Emulator"
}
```

Response (example):

```json
{
  "status": "paired",
  "agent_id": "agt_f39c14968e14497d",
  "relay_base_url": "http://127.0.0.1:8000/relay/agt_f39c14968e14497d",
  "idempotent": false,
  "relay_secret": "shared-once-secret",
  "relay_secret_shared": false
}
```

Android should store:

- `relay_base_url`
- `relay_secret`

Android then calls APIs like:

```bash
curl -s "$RELAY_BASE_URL/monitor/cpu" \
  -H "X-Secret: $RELAY_SECRET"
```

### 3) Agent polls status and activates

`GET /agents/register/status?agent_id=...` returns `PAIRED` plus a one-time `activation_token`.

`POST /agents/register/activate` with `{agent_id, activation_token}` returns:

- agent credential metadata
- the same `relay_secret` for agent-side persistent auth

### Security behavior

- Pairing code and PIN are stored hashed (raw values are not persisted)
- PIN attempt failures increment counters
- Session is blocked after repeated invalid PIN attempts
- Generic invalid response to prevent enumeration
- Re-pairing rotates the relay secret (new secret issued)

## Runtime authentication model

- Client/Android relay requests: `X-Secret: <relay_secret>`
- Agent reconnect token issuance: `POST /agents/{agent_id}/ws-token` with `X-Secret`
- Tunnel connection: `WebSocket /tunnel?agent_id={agent_id}&token={ws_token}`

## API summary

- `GET /health`
- `POST /agents/register/start`
- `GET /agents/register/status`
- `POST /pair/complete`
- `POST /agents/register/activate`
- `POST /agents/{agent_id}/revoke`
- `GET|POST|PUT|PATCH|DELETE /relay/{agent_id}/{path}`
- `GET /agents`
- `GET /agents/{agent_id}`
- `POST /agents/{agent_id}/ws-token`
- `POST /register`
- `WebSocket /tunnel?agent_id={agent_id}&token={ws_token}`

## Legacy flow

`POST /register` is retained for backward compatibility and can be disabled with:

- `vps.legacy_registration_enabled: false` in `config.yaml`
