# velavps — Ecosystem Architecture Documentation

## 1. Purpose & Role

velavps is the public-facing relay broker that sits between the internet and the user's NAT-isolated local Vela agent. Its specific responsibility in the ecosystem is to make a desktop PC agent remotely controllable from the Vela Android app without ever exposing the PC directly: it hosts the pairing-based onboarding flow (code + PIN) that binds an Android client and a PC agent to a shared `relay_secret`, maintains a persistent reverse-tunnel WebSocket from each connected agent, authenticates every client call against that secret, and forwards authorized requests to the correct agent — either over the live tunnel or via direct HTTP when the agent advertises a public address. It also owns the FCM push channel (device token registry + delivery) and connectivity alerting, so that phones are notified when their agent goes offline or comes back. In short, velavps is the trust boundary and control-plane for the whole Vela remote-control system: it is the only component that knows how to map a public client to a private agent, and it deliberately keeps agent authentication intact by injecting the agent's own secret into forwarded requests rather than bypassing it.

## 2. Key Modules & Responsibilities

- **`app/routers/vela_relay.py`**: The primary API surface. Implements the pairing lifecycle (`/agents/register/start`, `/pair/complete`, `/agents/register/activate`), the authenticated relay catch-all (`/relay/{agent_id}/{path}`), the agent tunnel WebSocket (`/tunnel`), push device registration/sending, and the public OAuth callback relay. This is where tenant isolation (`_verify_agent_access`) and per-IP/fingerprint rate limiting are enforced.
- **`app/services/vela_database.py`**: SQLite persistence layer holding the entire security model: `secrets`, `agents` (composite PK `(secret, agent_id)`), `agent_pairing_sessions` (hashed code/PIN, activation tokens, relay secret delivery timestamps), `ws_tokens`, `agent_credentials` (scoped, hashed, revocable), `push_devices`, and `audit_events`. All pairing/activation/revocation state machines live here.
- **`app/services/vela_agent_registry.py`**: In-memory registry of live `AgentConnection` objects. Binds each agent's WebSocket, tracks `connected`/`last_seen`, holds per-agent `pending_responses` futures and `pending_streams` for request/response correlation, and validates short-lived WebSocket tokens (with DB fallback for reconnection after restart).
- **`app/services/vela_forwarder.py`**: The forwarding engine. Resolves an agent (waiting up to `agent_connect_wait_seconds` if it is mid-connect), injects the agent's own `X-Secret` into outbound requests, and chooses direct HTTP forwarding vs. reverse-tunnel WebSocket forwarding, including streaming relay (`forward_response_start`/`chunk`/`end`) for SSE-style endpoints like `assistant/stream`.
- **`app/services/vela_push.py` + `app/services/vela_connectivity.py`**: FCM delivery to paired Android devices (device registry, token invalidation cleanup, agent-labeled titles) and the connectivity monitor that schedules offline/back-online push alerts after a configurable delay with a 5-minute cooldown.
- **`app/services/vela_settings.py`**: Loads `config.yaml` plus `.env` overrides (notably the FCM service-account path) into typed `Settings`/`VPSSettings` models.

## 3. External Interfaces

### Incoming (consumed by Vela agents, vela-android, and external clients)

All REST endpoints are FastAPI/HTTP with JSON bodies. Auth is via the `X-Secret` header (the shared `relay_secret`) unless noted.

| Endpoint | Protocol | Auth | Consumer |
|---|---|---|---|
| `GET /health` | REST/HTTP | none | any |
| `POST /agents/register/start` | REST/HTTP | none (rate-limited by IP+fingerprint) | local agent |
| `GET /agents/register/status?agent_id=` | REST/HTTP | none | local agent (polls for `PAIRED` + activation token) |
| `POST /pair/complete` | REST/HTTP | pairing code + PIN (no header) | vela-android |
| `POST /agents/register/activate` | REST/HTTP | one-time `activation_token` | local agent |
| `POST /agents/{agent_id}/revoke` | REST/HTTP | `X-Secret` | client/agent |
| `GET /relay/{agent_id}/status` | REST/HTTP | `X-Secret` | vela-android (live tunnel connectivity) |
| `POST /relay/{agent_id}/push/devices` / `DELETE` | REST/HTTP | `X-Secret` | vela-android (FCM token register/unregister) |
| `POST /relay/{agent_id}/push/send` | REST/HTTP | `X-Secret` | vela-android |
| `POST /agents/{agent_id}/push/send` | REST/HTTP | `X-Secret` | local Vela backend (PC-originated alerts) |
| `GET\|POST\|PUT\|PATCH\|DELETE /relay/{agent_id}/{path}` | REST/HTTP | `X-Secret` | vela-android / external clients (forwarded to agent) |
| `GET /relay/{agent_id}/callback` | REST/HTTP | none (single-use OAuth `code`) | OAuth providers (e.g. Spotify) |
| `GET /agents`, `GET /agents/{agent_id}` | REST/HTTP | `X-Secret` | client |
| `POST /agents/{agent_id}/ws-token` | REST/HTTP | `X-Secret` | local agent (reconnect token issuance) |
| `POST /register` | REST/HTTP | none (legacy, feature-flagged) | legacy agents |
| `WS /tunnel?agent_id=&token=` | WebSocket | short-lived `ws_token` (query or `x-agent-token` header) | local agent |

### Outgoing (calls made to databases, third-party services, or other Vela components)

| Target | Protocol | Auth | Data format | Purpose |
|---|---|---|---|---|
| Local SQLite (`vela.db`) | SQLite (WAL) | file-local | SQL rows | Persistence of secrets, agents, pairing sessions, ws tokens, credentials, push devices, audit log |
| Local agent (direct) | HTTP via `httpx` | agent's own `X-Secret` injected | JSON/binary | Direct forwarding fallback when agent has a public address |
| Local agent (tunnel) | WebSocket JSON messages | bound to validated tunnel session | JSON (`forward_request`, `forward_response`, `forward_response_start/chunk/end`) | Reverse-tunnel request/response + streaming relay |
| Firebase Cloud Messaging | HTTPS (firebase-admin SDK) | Firebase Admin service-account key | FCM message (notification + data payload) | Push delivery to registered Android devices |

## 4. Multitenancy & Security Model

Isolation is implemented at the code level through a **secret-as-identity** model combined with strict per-agent ownership checks:

- **Secret-as-identity**: Every tenant/user is identified by a unique `relay_secret` stored in the `secrets` table. The `agents` table uses a composite primary key `(secret, agent_id)`, so an agent record is always owned by exactly one secret. `list_agents(secret)` and `get_agent(agent_id, secret)` scope all queries by the caller's secret.
- **Cross-tenant enforcement (`_verify_agent_access`)**: Every relayed call first resolves the agent with `db.get_agent(agent_id, secret)`. If the agent exists but is owned by a *different* secret, the VPS returns `403 "Access denied: this agent belongs to another user"`; if it does not exist at all, it returns `404`. This is the core isolation boundary — a client can never reach an agent it does not own.
- **Pairing security**: Pairing code and PIN are stored only as SHA-256 hashes (raw values never persisted). PIN failures increment `attempt_count`; after 5 failures the session and agent are marked `EXPIRED`. A generic `invalid_or_expired_code` response is returned for all failure modes to prevent enumeration. Pairing code and activation token have TTLs (`pairing_code_ttl_seconds`, `activation_token_ttl_seconds`) and are single-use.
- **Secret delivery & rotation**: The `relay_secret` is delivered exactly once to the client at `/pair/complete` and once to the agent at `/agents/register/activate` (tracked via `client_secret_delivered_at` / `agent_secret_delivered_at`; re-delivery returns `409 secret_already_delivered`). Re-pairing an existing agent rotates the secret, invalidating the old one.
- **WebSocket session binding**: The agent tunnel is authenticated with a short-lived (60s) `ws_token` issued via `/agents/{agent_id}/ws-token` (or at registration). Tokens are validated against the in-memory registry with a persisted fallback in the `ws_tokens` table so agents can reconnect after a VPS restart. The WebSocket is bound to a specific `agent_id`, and all tunnel messages are correlated to that agent's pending request futures/streams.
- **Agent credentials**: On activation, a scoped credential (`agent:relay`, `agent:heartbeat`, `agent:ws`) is created, stored hashed, and can be revoked via `/agents/{agent_id}/revoke` (which also marks the agent `REVOKED`).
- **Preserving agent auth**: The forwarder injects the agent's own `X-Secret` into forwarded requests (`_inject_agent_secret`), so the local agent's own authentication is never bypassed — the relay is a trusted proxy, not an auth bypass.
- **Hardening**: Admin endpoints were removed to prevent IDOR; rate limiting is applied via `slowapi` (global) plus an in-memory limiter keyed by IP and device fingerprint on pairing endpoints; CORS is permissive but all sensitive routes require the secret header.

## 5. Configuration & Environment Variables

Config lives in `config.yaml` under the `vps:` key, with `.env` overrides for secrets. Key settings relevant to ecosystem integration:

- **`vps.allow_direct_agent_forwarding`** (bool, default `true`): Whether the relay may forward directly to an agent's `public_address` over HTTP, or must use the reverse tunnel only. Set `false` to force tunnel-only (safer for NAT'd agents).
- **`vps.default_agent_timeout_seconds`** (int, default `20`): Timeout for agent forwarding and stream start; also the httpx client timeout.
- **`vps.agent_connect_wait_seconds`** (int, default `8`): How long the relay waits for an agent to establish its tunnel before returning `503` on a relayed call.
- **`vps.rate_limit`** (str, default `100/minute`): Global slowapi limit applied to all endpoints.
- **`vps.legacy_registration_enabled`** (bool, default `true`): Enables/disables the legacy `POST /register` flow (returns `410` when disabled).
- **`vps.pairing_code_ttl_seconds`** (int, default `600`): Lifetime of the pairing code/PIN session.
- **`vps.activation_token_ttl_seconds`** (int, default `180`): Lifetime of the one-time activation token.
- **`vps.fcm_service_account_path`** (str): Path to the Firebase Admin service-account JSON used for push. Overridable via env.
- **`vps.connectivity_offline_delay_seconds`** (int, default `30`): Delay after tunnel disconnect before an offline push is sent (avoids flap noise).
- **`vps.api_keys`** (list): Admin API keys (currently only used by the disabled admin router).
- **`VELAVPS_FCM_SERVICE_ACCOUNT_PATH`** (env, also accepts `FCM_SERVICE_ACCOUNT_PATH`): Overrides `fcm_service_account_path`. Must point to a Firebase Admin service-account key (`"type": "service_account"`), not `google-services.json`.

## 6. Integration Contract with Other Repos

### What velavps EXPECTS from Vela (local PC agent)

- The agent initiates onboarding by calling `POST /agents/register/start`, polls `GET /agents/register/status` until `PAIRED`, and completes activation via `POST /agents/register/activate` with the one-time token, receiving its `relay_secret` and scoped credential.
- The agent opens `WS /tunnel?agent_id=...&token=...` (token from `/agents/{agent_id}/ws-token`) and speaks the tunnel protocol: sends `heartbeat` messages, handles incoming `forward_request` messages, and replies with `forward_response` (or `forward_response_start`/`forward_response_chunk`/`forward_response_end` for streaming endpoints such as `assistant/stream`).
- The agent exposes the same REST API paths that clients call through `/relay/{agent_id}/{path}` (system control, media, filesystem, scheduler, maintenance, etc.) and authenticates those with its own `X-Secret` (injected by the relay).
- The agent exposes a public `GET /spotify/callback` landing page so OAuth callbacks relayed by the VPS can complete.
- The agent (or its local backend) sends PC-originated alerts via `POST /agents/{agent_id}/push/send` with `X-Secret`.

### What velavps EXPECTS from vela-android

- The Android app completes pairing via `POST /pair/complete` with the code + PIN, then stores the returned `relay_base_url` and `relay_secret`.
- It calls all remote-control APIs as `GET|POST|PUT|PATCH|DELETE {relay_base_url}/{path}` with the `X-Secret` header.
- It registers/unregisters its FCM token via `POST`/`DELETE /relay/{agent_id}/push/devices` and polls `GET /relay/{agent_id}/status` to reflect live tunnel connectivity.
- It can send push via `POST /relay/{agent_id}/push/send`.

### What velavps EXPECTS from vela-mcp

- vela-mcp runs on the local agent/PC and is the consumer of the agent's API that gets invoked through the tunnel. velavps expects vela-mcp's tool endpoints to be reachable at the agent's relayed paths and to honor the agent's `X-Secret` auth, so that a client call relayed through the VPS reaches the MCP tool and returns its response (including streaming SSE responses) intact.