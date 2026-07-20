# Server Deployment Guide

> 文件層級：內部維運附錄。正式產品基線與發布邊界請見 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

This guide deploys the API and four MCP services on one Linux host. The same process layout can be
split across hosts later without changing the public tool contract.

## 1. Network layout

| Process | Default port | Exposure |
|---|---:|---|
| FastAPI | 8000 | Reverse proxy or private network |
| Knowledge MCP | 8001 | Internal only |
| Finance MCP | 8002 | Internal only |
| Verified Financial MCP | 8003 | External Agent through protected gateway |
| Verified Earnings Call MCP | 8004 | External Agent through protected gateway |
| Frontend dev server | 5173 | Development only; deploy `frontend/dist` in production |

Never expose ports 8001 or 8002 publicly.

## 2. Install and configure

```bash
git clone <repository-url> verified-rag
cd verified-rag
make setup
cp .env.example .env
```

Minimum private-server settings:

```dotenv
APP_ENV=production
DATA_MODE=local
API_HOST=0.0.0.0
API_PORT=8000
MCP_SERVER_HOST=127.0.0.1
MCP_BIND_HOST=0.0.0.0
MCP_AUTH_MODE=static
MCP_SHARED_TOKEN=<long-random-secret>
CORS_ORIGINS=https://rag.example.com
FRONTEND_USE_PUBLIC_MCP=true
```

`MCP_SERVER_HOST` is the address used by local services to call each other.
`MCP_BIND_HOST` is the listening interface. Keep external firewall rules limited to 8000, 8003 and
8004, or preferably expose only an authenticated reverse proxy.

Static MCP tokens are suitable for a private/VPN deployment baseline. Internet-facing systems
should use organization OAuth/OIDC at an API gateway and rotate secrets through a secret manager.

Configure SQLite/Neo4j/Ollama and optional external providers using
[EXTERNAL_INTEGRATION_GUIDE.md](EXTERNAL_INTEGRATION_GUIDE.md).

## 3. Initialize and verify data

```bash
cd backend
../.venv/bin/python -m scripts.init_sqlite               # create/migrate Financial Schema v2
# Use --seed-demo only for a new demo database.
../.venv/bin/python -m scripts.init_data                 # requires Neo4j and Ollama
../.venv/bin/pytest -q
../.venv/bin/python -m scripts.evaluate_sec
../.venv/bin/python -m scripts.evaluate_transcripts
../.venv/bin/python -m scripts.evaluate_llm_behavior --live-llm
cd ../frontend
npm run build
```

Only run `--live-llm` after configuring the production LLM. Evidence-only deployment does not
require it and should expose only the `retrieve_*_evidence` tools to its Agent policy.

## 4. Start the backend stack

Foreground:

```bash
.venv/bin/python backend/scripts/run_local.py --no-frontend
```

The supervisor starts all four MCP processes and FastAPI, checks each port and terminates the whole
stack when one child exits.

Example systemd unit `/etc/systemd/system/verified-rag.service`:

```ini
[Unit]
Description=Verified Financial RAG API and MCP services
After=network-online.target neo4j.service ollama.service
Wants=network-online.target

[Service]
Type=simple
User=verified-rag
Group=verified-rag
WorkingDirectory=/opt/verified-rag
EnvironmentFile=/opt/verified-rag/.env
ExecStart=/opt/verified-rag/.venv/bin/python backend/scripts/run_local.py --no-frontend
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now verified-rag
sudo systemctl status verified-rag
```

For independent scaling, run each `python -m mcp_servers.*` and Uvicorn command under separate
service units. Keep 8001/8002 private and update `KNOWLEDGE_MCP_URL`/`FINANCE_MCP_URL` when moved.

## 5. Deploy the frontend

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

Serve `frontend/dist` from Nginx/CDN. The frontend expects the HTTP API under `/api`; configure the
same-origin reverse proxy or the frontend's deployment-time API base URL as applicable.

Nginx API example:

```nginx
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_read_timeout 120s;
}
```

MCP Streamable HTTP must preserve request headers and streaming. Route each endpoint separately in
your authenticated gateway; do not merge Financial and Transcript MCP into one upstream.

## 6. Post-deployment smoke test

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/health/readiness

curl -fsS -X POST http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"query":"Microsoft 最近一季 revenue?"}'

cd backend
../.venv/bin/python -m scripts.smoke_mcp
```

When `MCP_AUTH_MODE=static`, MCP clients must send:

```text
Authorization: Bearer <MCP_SHARED_TOKEN>
```

Acceptance criteria:

- liveness returns HTTP 200;
- readiness accurately reports `ready` or `evidence_only_ready`;
- an available-period query is `answered` and `verified=true`;
- a future-period query is `refused` with zero citations;
- Financial MCP never cites transcripts;
- Earnings Call MCP cites transcripts only;
- source previews resolve using citation `source_id` and `co_code`.

## 7. Operations

- Run Golden Sets after every data refresh, mapping change, model change or schema change.
- Monitor HTTP status, domain status, latency, queue 503 rate, provider timeouts and refusal rate.
- Back up SQLite raw snapshots, Neo4j data and provider mapping versions together.
- Rotate API/MCP/DB secrets without committing `.env` or local registry files.
- Scale API workers only after confirming downstream LLM, Ollama, Neo4j and provider capacity.
- Regenerate `docs/openapi.json` with `make export-api` for every HTTP contract change.
- With public MCP services running, regenerate `docs/mcp-tools.json` with `make export-mcp` for every Tool contract change.

## 8. Rollback

Keep the previous application release, `.env` schema, provider mapping and data version. Roll back
all four MCP services and the API as one compatible unit unless the changed tool was explicitly
versioned. Do not deploy a required-field MCP change without a major contract version or coordinated
client rollout.
