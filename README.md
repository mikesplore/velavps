# VPS Relay Service

This repository implements a VPS relay service for the local Vela agent.
It is designed to accept public client requests, authenticate them, and forward authorized calls to one or more registered local agents.

## Features

- Public API authentication via API keys
- Local agent registration and heartbeat
- Reverse-tunnel forwarding via WebSocket when agents are connected
- Direct HTTP forwarding fallback when local agent public address is available
- Preservation of local agent response status and body

## Quick start

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Configure `config.yaml`.

3. Start the app:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Alternative: use the included script to create a virtual environment, install dependencies, and launch the app.

```bash
./start.sh
```

4. Use `GET /health` to verify availability.

## API

- `GET /health`
- `POST /register`
- `WebSocket /tunnel?agent_id={agent_id}&token={ws_token}`
- `POST /relay/{agent_id}/{path}`

## Notes

The relay attaches `X-VPS-Auth` to forwarded requests so the local agent can authenticate the VPS layer separately from client auth.
