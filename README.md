<div align="center">

<img src="docs/logo.svg" alt="Satyaki Solutions" width="180"/>

# incident-buffer

**Bounded ring-buffer incident capture — one dashboard, every service.**

_A [Satyaki Solutions Pvt Ltd](https://satyaki.co.in) open-source project — Creating Innovative Impulse_

[![Python](https://img.shields.io/badge/python-3.10%2B-458DFF?logo=python&logoColor=white)](https://www.python.org)
[![Node](https://img.shields.io/badge/node-18%2B-458DFF?logo=node.js&logoColor=white)](https://nodejs.org)
[![Docker](https://img.shields.io/badge/docker-single%20container-458DFF?logo=docker&logoColor=white)](#docker--single-container)
[![License](https://img.shields.io/badge/license-MIT-FCA719)](LICENSE)
[![Website](https://img.shields.io/badge/satyaki.co.in-FCA719?logo=firefox&logoColor=white)](https://satyaki.co.in)

</div>

---

## What it is

A tiny incident-capture library plus a single-container dashboard. Every
service on your stack — Python backends, Node workers, browser frontends,
CLI scripts — keeps the last **N events per process** in a bounded ring
buffer. On an error, the buffer is frozen into a self-contained JSON
envelope and posted to one central dashboard where you (or an on-call
teammate) can replay exactly what led up to the failure.

Concretely, this project ships:

| Component | Language | Distribution |
|---|---|---|
| Core client library | Python 3.10+ | `pip install "incident-buffer"` |
| Dashboard server + UI (optional extra) | Python + vanilla HTML/CSS/JS | `pip install "incident-buffer[dashboard]"` |
| Companion library for Node / [pino](https://github.com/pinojs/pino) | Node 18+ | `npm i @satyaki/incident-buffer` |
| Browser client (single file, no build step) | Vanilla JavaScript | `<script src="…/static/incident-buffer.js"></script>` |
| Docker image | — | one image, one port, one volume |

All four clients share the exact same envelope schema, so a single dashboard
serves incidents from your whole stack.

---

## Table of contents

1. [Why](#why)
2. [Architecture — one dashboard, many services](#architecture--one-dashboard-many-services)
3. [Quick start (Docker)](#quick-start-docker)
4. [Quick start (Python only)](#quick-start-python-only)
5. [Wiring guides](#wiring-guides) — [Python backend](#wire-a-python-backend), [Node worker](#wire-a-node-worker), [Browser frontend](#wire-a-browser-frontend)
6. [Envelope schema (v1)](#envelope-schema-v1)
7. [Dashboard UI](#dashboard-ui)
8. [HTTP API reference](#http-api-reference)
9. [Operations](#operations) — retention, tokens, CORS, reverse proxy
10. [Security posture](#security-posture)
11. [Development](#development)
12. [Roadmap](#roadmap)
13. [About Satyaki](#about-satyaki)

---

## Why

Every production incident report starts with the same question: *what was
the code doing right before it broke?* Stdout / journald / cloud logging
answer that in aggregate — you go trawling for a needle in the whole
service. This library gives you the same signal, **scoped to the failing
request or job**, with zero configuration for the reader.

- A bounded ring buffer means memory stays flat — no unbounded queues, no
  Redis, no side-database.
- On error, the whole envelope (events, exception, correlation ids,
  eviction/truncation stats) is committed to disk atomically, so log
  shipping outages never lose evidence you already captured.
- One dashboard container per environment ingests envelopes from every
  service that imports the library — pip, npm, or a `<script>` tag.

The dashboard is deliberately read-only and honest: *"No incidents
recorded" does not prove no errors occurred* — the UI says so out loud.

---

## Architecture — one dashboard, many services

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Frontend    │    │ FastAPI BE  │    │ Node worker │    │ CLI script  │
│ (browser)   │    │  (Python)   │    │  (Pino)     │    │             │
│             │    │             │    │             │    │             │
│ <script>    │    │ pip install │    │ npm install │    │ pip install │
│ ib.capture()│    │ ib.capture()│    │ ib.capture()│    │ ib.capture()│
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │                   │
       │  HTTPS  POST /api/ingest   (Bearer <ingest-token>)      │
       └──────────────┬───┴──────────────────┴───────────────────┘
                      ▼
           ┌──────────────────────┐
           │  DASHBOARD CONTAINER │   one per team / env
           │  port 8765           │
           │  SQLite index        │
           │  JSON evidence files │
           └──────────────────────┘
                      │
                      │  GET /api/incidents  (list, search, filter)
                      │  GET /api/events     (SSE — live updates)
                      ▼
              your admin UI, Slack bot, alert bot, human on-call
```

**Rule of thumb:** the dashboard is a single central service (one per env).
Every app service imports the client library and either:

- POSTs to `/api/ingest` over the network (works across hosts), or
- writes to a **shared volume** the dashboard container also mounts (works
  when the app runs on the same host — no network hop).

Mix per service.

---

## Quick start (Docker)

Fastest path: one container running everything the server side needs.

```bash
git clone https://github.com/neeraj-satyaki/incident-buffer.git
cd incident-buffer

docker build -t incident-buffer:0.1.0 .

docker run -d --name incident-buffer \
  -p 8765:8765 \
  -v "$PWD/incidents:/data" \
  -e INCIDENT_BUFFER_INGEST_TOKEN=change-me \
  -e INCIDENT_BUFFER_CORS_ORIGINS='*' \
  incident-buffer:0.1.0
```

Or with docker-compose:

```bash
INCIDENT_BUFFER_INGEST_TOKEN=change-me docker compose up -d
```

Open **http://localhost:8765** — the dashboard is live.

Populate with synthetic incidents to see the UI in action:

```bash
docker exec incident-buffer incident-buffer demo --data-dir /data
```

Refresh the browser — two incidents (`reimbursement-api` and `batch-worker`)
appear in the list.

### Container env vars

| Variable | Default | Purpose |
|---|---|---|
| `INCIDENT_BUFFER_DATA_DIR` | `/data` | Where JSON evidence files live |
| `INCIDENT_BUFFER_INGEST_TOKEN` | *(unset)* | Required to accept `POST /api/ingest` |
| `INCIDENT_BUFFER_VIEWER_TOKEN` | *(unset)* | If set, dashboard UI requires this Bearer token |
| `INCIDENT_BUFFER_MAX_PAYLOAD` | `1048576` | Byte cap on ingest bodies |
| `INCIDENT_BUFFER_CORS_ORIGINS` | `*` | Comma-separated allowed origins (lock down in prod) |

The image contains ONLY the dashboard server. Your app containers (Python,
Node, browser code) import the client library — they do *not* run inside
this image.

---

## Quick start (Python only)

If you would rather run it directly (development, quick trial, embedded in
another Python service):

```bash
pip install "incident-buffer[dashboard]"
# or with uv
uv add "incident-buffer[dashboard]"

incident-buffer demo --data-dir ./incidents
incident-buffer serve \
  --host 127.0.0.1 --port 8765 \
  --data-dir ./incidents \
  --ingest-token demo
```

Open **http://localhost:8765** — same UI as the container.

---

## Wiring guides

### Wire a Python backend

Install (core client only, no dashboard):

```bash
pip install incident-buffer
```

Use it as a logger + capture site:

```python
from incident_buffer import IncidentBuffer, HttpExporter

# ONE buffer per process. Keep it as a module-level singleton.
ib = IncidentBuffer(
    service="reimbursement-api",
    service_version="1.4.0",
    data_dir="/var/lib/incident-buffer",     # local disk on this pod
    capacity=500,                            # ring size
)

# OPTIONAL: forward captured envelopes to the central dashboard.
exporter = HttpExporter(
    url="https://dash.internal:8765/api/ingest",
    auth_token=os.environ["INCIDENT_BUFFER_INGEST_TOKEN"],
)
ib = IncidentBuffer(..., on_capture=lambda env, path: exporter.enqueue(path))
```

Wire it into your FastAPI app:

```python
from fastapi import FastAPI, Request
app = FastAPI()

@app.middleware("http")
async def add_req_id(request: Request, call_next):
    request.state.req_id = request.headers.get("x-request-id") or ib_uuid()
    return await call_next(request)

@app.exception_handler(Exception)
async def catch_all(request: Request, exc: Exception):
    ib.capture(
        trigger="fastapi_exception",
        error=exc,
        request_id=getattr(request.state, "req_id", None),
    )
    raise exc

@app.get("/reimburse")
def reimburse():
    ib.debug("Session loaded", session_id="s_ab12")
    ib.debug("Tariff lookup returned no match", region="MH")
    raise HTTPException(500, "No matching tariff")
```

### Wire a Node worker

```bash
npm install @satyaki/incident-buffer pino
```

```javascript
const pino = require('pino');
const { IncidentBuffer, HttpExporter } = require('@satyaki/incident-buffer');

const ib = new IncidentBuffer({
  service: 'checkout-node',
  serviceVersion: '2.1.0',
  dataDir: process.env.IB_DATA_DIR || './incidents',
  capacity: 500,
  exporter: new HttpExporter({
    url: 'https://dash.internal:8765/api/ingest',
    authToken: process.env.IB_INGEST_TOKEN,
  }),
});

// Route pino output into the ring buffer:
const log = pino({ level: 'debug' }, ib.stream());

log.debug({ cart_id: 'c_991' }, 'cart loaded');
log.warn({ latency_ms: 2100 }, 'payment provider slow');

try {
  await capturePayment(intent);
} catch (err) {
  ib.capture({ trigger: 'unhandled', error: err, requestId: req.id });
  throw err;
}

process.on('uncaughtException', (err) => {
  ib.capture({ trigger: 'uncaughtException', error: err });
  process.exit(1);
});
```

### Wire a browser frontend

```html
<script src="https://dash.internal:8765/static/incident-buffer.js"></script>
<script>
  const ib = new IncidentBuffer({
    ingestUrl: "https://dash.internal:8765/api/ingest",
    ingestToken: "<served from your CSP-safe config endpoint>",
    service: "web-frontend",
    serviceVersion: "3.0.5",
    capacity: 300,
  });
  ib.installGlobalHandlers();   // window.onerror + unhandledrejection + console.error

  // Elsewhere in your app:
  ib.debug("cart loaded", { items: 3 });
  ib.warn("promotion expired", { code: "FALL10" });

  try {
    doThing();
  } catch (err) {
    const id = ib.capture({ trigger: "user_action", error: err });
    console.log("captured", id);
  }
</script>
```

Live example: [`demo/frontend-demo.html`](demo/frontend-demo.html) — open in
any browser after the dashboard container is running, click **Trigger
error**, refresh the dashboard: a `web-frontend` incident appears.

---

## Envelope schema (v1)

Every client — Python, Node, browser — emits envelopes of exactly this
shape. The dashboard validates the `schema_version` field and rejects
mismatched versions with HTTP 400.

```jsonc
{
  "schema_version": "1",
  "incident_id": "inc_2f7032eb9051458b",
  "ts_ms": 1788863271583,
  "service": "reimbursement-api",
  "service_version": "1.4.0",
  "library_version": "0.1.0",
  "host": "svc-01",
  "pid": 12345,

  "request_id": "req_abc123",
  "job_id":     null,
  "trace_id":   "trace_dddddddddddd",

  "trigger": "fastapi_exception",
  "error": {
    "type":    "ValueError",
    "message": "No matching tariff for region=MH plan=TOU-A",
    "stack":   "Traceback (most recent call last):\n  ..."
  },

  "evidence": {
    "events": [
      { "ts_ms": 1788863270120, "severity": "DEBUG",
        "message": "Session loaded", "fields": { "session_id": "s_ab12" } },
      { "ts_ms": 1788863271245, "severity": "DEBUG",
        "message": "Meter reading accepted", "fields": { "reading_kwh": 42.7 } },
      { "ts_ms": 1788863271400, "severity": "DEBUG",
        "message": "Tariff lookup returned no match", "fields": { "region": "MH" } }
    ],
    "event_count":   3,
    "evicted_count": 0,      // events discarded because the ring was full
    "dropped_count": 0,      // envelopes the exporter had to drop
    "truncated":     false,  // any oversized field was clipped
    "capacity":      500
  },

  "capture_config": { "capacity": 500, "max_field_bytes": 8192 },
  "metadata": { "any": "app-supplied key-value pairs" }
}
```

**Truths the schema enforces:** if the ring buffer was already full when
new events arrived, `evicted_count > 0` — the dashboard shows an *"earlier
history is incomplete: N records were evicted before capture"* warning so
readers never mistake a truncated timeline for a complete one.

---

## Dashboard UI

<div align="center">

_Compact, honest, keyboard-friendly. One main screen: incident list on the
left, selected incident details on the right._

</div>

The layout uses:

- Background `#F6F8FB`, primary text `#172B4D`, primary action `#458DFF`
  (Satyaki Blue), amber for warnings via `#FCA719` (Satyaki Gold), red
  reserved for errors.
- System font stack for text; monospace for identifiers and logs.
- On narrow widths, the list swaps to a detail view with a **← Back**
  button.

**Top bar**
- Product name, service filter, time-range filter, search field.
- Live-updates toggle (SSE).
- Connection status pill — distinguishes *"connected to dashboard"* from
  *"application healthy"*.

**Incident list** (compact table)
| Time | Service | Error | Request / Job | Evidence |
|---|---|---|---|---|
| 10:42:03 | reimbursement-api | No matching tariff | req_abc123 | 4 records |

Newest first, server-side pagination, evidence warning shown when records
were dropped or truncated. New arrivals surface as a *"N new incidents"*
banner — they never replace the current selection.

**Detail pane — three tabs**

1. **Timeline** — retained events in chronological order, with the
   triggering event highlighted and time deltas relative to it. Filter by
   severity, search within the incident, expand an event to inspect
   structured fields, copy an event as JSON. If evidence is incomplete,
   the panel names *why* ("Earlier history is incomplete: 120 records
   were evicted before capture."). Panel is labelled **"Captured
   evidence"** — it does not invent a root cause.

2. **Exception** — type, message, stack trace, copy button. Local
   variables are NOT captured by default.

3. **Metadata** — correlation identifiers, service / version / host / pid,
   capture configuration, trigger reason, eviction / drop / truncation
   counts. Credentials and exporter tokens are never displayed.

---

## HTTP API reference

All endpoints return JSON except `/api/events` (SSE) and the download
endpoint (raw envelope file).

### `GET /api/health`
Dashboard reachability + version + indexed services. Explicitly notes that
*dashboard reachability is not application health.*

### `GET /api/incidents`
List incidents with filters + cursor pagination.

Query params:

| Param | Type | Purpose |
|---|---|---|
| `service` | string | Filter by service name |
| `since_ms`, `until_ms` | int (epoch ms) | Time window |
| `q` | string | Match against incident id, req/job/trace id, error message |
| `limit` | int (1-500) | Page size |
| `cursor_ts` | int | `ts_ms` of the previous page's last row |

Response: `{ "incidents": [...], "next_cursor_ts": number | null }`

### `GET /api/incidents/{incident_id}`
Full envelope + index row.

### `GET /api/incidents/{incident_id}/download`
The raw JSON envelope file (Content-Disposition `attachment`).

### `POST /api/ingest`
Accepts one envelope. Headers:

```
Content-Type: application/json
Authorization: Bearer <INCIDENT_BUFFER_INGEST_TOKEN>
```

Behaviours:

- Validates `schema_version` — mismatched versions rejected 400.
- Enforces `INCIDENT_BUFFER_MAX_PAYLOAD` byte cap — 413 on overflow.
- **Idempotent on `incident_id`** — a byte-identical retry returns 200.
- **Conflict on `incident_id`** — a different body under the same id
  returns 409.
- Commits the envelope atomically (`.tmp` file → `rename`) before
  acknowledging. Acknowledged incidents are guaranteed on disk.
- Reconciling clients that reconnect after downtime will re-see missed
  notifications by re-listing.

### `GET /api/events` (SSE)
Server-Sent Events stream. Emits `event: incident.new` with
`{ incident_id, ts_ms }` when a new envelope lands.

Subscriber queues are bounded; keepalive pings prevent stale connections.
Reconnecting clients receive a `hello` frame with `{ "refresh": true }`
telling them to re-pull the list — no missed incident is lost.

---

## Operations

### Retention

Incidents live on disk under `{data_dir}/YYYY/MM/DD/inc_*.json`. This
project does not delete files it did not write in the current process.
Retention is a cron / systemd concern:

```bash
# retain 90 days
find /data -type f -name 'inc_*.json' -mtime +90 -delete
```

The SQLite index is rebuilt from the file tree on server start (bounded to
50 000 files per scan). Deleting the sqlite file is safe.

### Rotating tokens

Ingest and viewer tokens live in the container environment. To rotate:

1. Redeploy the dashboard with the new `INCIDENT_BUFFER_INGEST_TOKEN`.
2. Roll the new token out to each service that ships incidents.

There is no shared-secret registry — that is deliberate.

### CORS (for browser clients)

`INCIDENT_BUFFER_CORS_ORIGINS` accepts a comma-separated allowlist. Lock
down in production:

```bash
docker run … \
  -e INCIDENT_BUFFER_CORS_ORIGINS="https://app.example.com,https://admin.example.com" \
  incident-buffer:0.1.0
```

### Behind a reverse proxy (nginx)

```nginx
server {
  listen 443 ssl http2;
  server_name dash.internal;

  location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_read_timeout 3600;    # SSE
    proxy_buffering off;         # SSE
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
  }
}
```

### Kubernetes

- One `Deployment` with the dashboard container.
- One `PersistentVolumeClaim` mounted at `/data`.
- Service account tokens sourced from a `Secret` and injected as env vars.
- Add a `NetworkPolicy` that only permits ingest traffic from your app
  namespaces.

---

## Security posture

- **Bind to `127.0.0.1` by default.** LAN / cluster exposure requires an
  explicit `--host 0.0.0.0` and paired token configuration.
- **Ingest requires `Bearer <token>`.** Missing or wrong token → 401.
- **Viewer token is optional** and only meaningful when the dashboard is
  exposed beyond localhost. Otherwise front with a reverse proxy that
  supplies its own auth.
- **All log content is treated as untrusted text** — the UI renders event
  messages and fields as text, never HTML.
- **Ingest byte cap** (`INCIDENT_BUFFER_MAX_PAYLOAD`, default 1 MiB)
  prevents runaway envelopes.
- **Data-dir traversal is blocked** on both reads and writes.
- **No credentials in URLs or browser local storage.**
- **No secret capture** — the client libraries do not capture local
  variables or process env by default.

The initial dashboard is designed for **trusted operators**. It does not
provide tenant-isolated customer access. If you need that, front it with a
gateway that terminates auth and enforces tenant scoping before the
request reaches the dashboard.

---

## Development

```bash
# Python
uv venv
uv pip install -e ".[dashboard,exporter,dev]"
uv run pytest -q

# Node
cd node
node -c index.js         # syntax
```

### Repository layout

```
incident-buffer/
├── src/incident_buffer/
│   ├── buffer.py                 # ring buffer + envelope + atomic disk write
│   ├── exporter.py               # background HTTP exporter (bounded, retried)
│   ├── cli.py                    # incident-buffer serve | demo
│   ├── demo.py                   # synthetic labelled DEMO incidents
│   ├── version.py                # __version__, SCHEMA_VERSION
│   ├── dashboard/
│   │   ├── app.py                # FastAPI wiring
│   │   └── index.py              # SQLite index
│   └── static/
│       ├── index.html            # dashboard UI
│       ├── style.css             # brand-adjacent palette
│       ├── app.js                # UI logic
│       └── incident-buffer.js    # browser client SDK
├── node/                         # @satyaki/incident-buffer companion
├── demo/                         # FastAPI + Node + browser demos
├── tests/
├── Dockerfile                    # python:3.12-slim + fastapi + uvicorn
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

---

## Roadmap

- Publish to PyPI and npm.
- Optional OpenTelemetry span import (pull existing spans into the ring
  buffer for parity across observability tooling).
- Ready-to-import Grafana panel definitions that read `/api/incidents`.
- Per-service capacity + severity floor via env / config file.
- Tenant-scoped viewer tokens.
- Read-only replicas — one central dashboard fanning to regional caches.

---

## About Satyaki

[Satyaki Solutions Pvt Ltd](https://satyaki.co.in) is an AI/ML and
industrial-CV consultancy building bespoke solutions across manufacturing,
logistics, and enterprise observability. This library is one of several
open-source pieces we ship — practical tools for teams that need to move
fast without giving up rigour.

**Website:** [satyaki.co.in](https://satyaki.co.in) · **Tagline:** *Creating
Innovative Impulse* · **Author:** [@neeraj-satyaki](https://github.com/neeraj-satyaki)

---

## Licence

MIT © Satyaki Solutions Pvt Ltd.

See [`LICENSE`](LICENSE) for the full text.
