# incident-buffer

Bounded ring-buffer incident capture, on-disk canonical evidence, tiny
FastAPI dashboard. Python + Node/pino companions share one envelope schema
so a single dashboard serves both.

## Quick start

```bash
# from local wheel
uv build
uv add "./dist/incident_buffer-0.1.0-py3-none-any.whl[dashboard]"

# once published:
# pip install "incident-buffer[dashboard]"

# 1. write a couple of demo incidents into ./incidents
uv run incident-buffer demo --data-dir ./incidents

# 2. run the dashboard
uv run incident-buffer serve \
  --host 127.0.0.1 --port 8765 --data-dir ./incidents \
  --ingest-token demo

# 3. open the UI
open http://127.0.0.1:8765
```

## Python — capture inside your app

```python
from incident_buffer import IncidentBuffer

ib = IncidentBuffer(
    service="reimbursement-api",
    service_version="1.4.0",
    data_dir="./incidents",
    capacity=500,
)

@app.exception_handler(Exception)
async def catch_all(request, exc):
    ib.capture(trigger="fastapi_exception", error=exc,
               request_id=request.state.req_id)
    raise exc

# elsewhere:
ib.debug("Session loaded", session_id="s_ab12")
ib.debug("Tariff lookup returned no match", region="MH")
```

Attach an HTTP exporter to forward completed incidents to a central dashboard:

```python
from incident_buffer import HttpExporter
exporter = HttpExporter(url="http://dashboards.local:8765/api/ingest",
                        auth_token="…")
ib = IncidentBuffer(..., on_capture=lambda env, p: exporter.enqueue(p))
```

## Node.js — pino companion

```bash
npm install @satyaki/incident-buffer pino
```

```javascript
const pino = require('pino');
const { IncidentBuffer, HttpExporter } = require('@satyaki/incident-buffer');

const exporter = new HttpExporter({
  url: 'http://127.0.0.1:8765/api/ingest', authToken: 'demo',
});
const ib = new IncidentBuffer({
  service: 'checkout-node', serviceVersion: '2.1.0',
  dataDir: './incidents', exporter,
});
const log = pino({ level: 'debug' }, ib.stream());

try { doStuff(); }
catch (err) {
  ib.capture({ trigger: 'unhandled', error: err, requestId: req.id });
  throw err;
}
```

## Dashboard

- Bind to `127.0.0.1` by default. For remote use, front with a reverse
  proxy that supplies its own TLS and viewer auth (or pass
  `--viewer-token`).
- One main screen: incident list on the left, incident detail on the right.
  Narrow widths swap to list-then-detail with a Back button.
- Right pane tabs: **Timeline**, **Exception**, **Metadata**.
- Live updates via SSE (`GET /api/events`). Reconnecting clients refresh
  the list to recover missed notifications.
- New arrivals do not clobber the current selection — they surface as a
  "N new incidents" banner.

### API

```text
GET  /api/incidents?service=&since_ms=&until_ms=&q=&limit=&cursor_ts=
GET  /api/incidents/{incident_id}
GET  /api/incidents/{incident_id}/download
GET  /api/events                 SSE
GET  /api/health
POST /api/ingest                 Bearer <ingest-token>
```

`POST /api/ingest` accepts the same envelope shape Python and Node emit
locally. Retries with the same `incident_id` are idempotent; a different
body under the same id yields **409 Conflict**.

## Envelope schema (v1)

```jsonc
{
  "schema_version": "1",
  "incident_id": "inc_…",
  "ts_ms": 1738400000000,
  "service": "reimbursement-api",
  "service_version": "1.4.0",
  "library_version": "0.1.0",
  "host": "svc-01", "pid": 12345,
  "request_id": "req_…", "job_id": null, "trace_id": null,
  "trigger": "fastapi_exception",
  "error": { "type": "ValueError", "message": "…", "stack": "…" },
  "evidence": {
    "events": [{ "ts_ms": 0, "severity": "DEBUG", "message": "…", "fields": {} }],
    "event_count": 42,
    "evicted_count": 0,
    "dropped_count": 0,
    "truncated": false,
    "capacity": 500
  },
  "capture_config": { "capacity": 500, "max_field_bytes": 8192 },
  "metadata": { }
}
```

## Truths the dashboard enforces

- "Dashboard connection" is distinct from "application healthy" — the
  connection pill in the topbar reflects the dashboard only.
- "No incidents recorded" is presented explicitly as *not* proof that no
  errors occurred.
- When evidence is incomplete, the timeline panel names why:
  *"Earlier history is incomplete: N records were evicted before capture."*
- Structured event fields render as data, not HTML. All log content is
  treated as untrusted.
- The initial dashboard is described as read-only for trusted operators; it
  does not provide tenant isolation.

## Storage + retention

- Incident files: `data_dir/YYYY/MM/DD/inc_*.json`. Writes are atomic
  (write-tmp then rename).
- SQLite index at `data_dir/_index.sqlite`; rebuilt from files on startup
  (bounded scan). Deleting the sqlite file is safe.
- Bring retention with cron / systemd timer — the app never deletes files
  it did not write in this session.

## Acceptance demo

```bash
# terminal 1 — dashboard
uv run incident-buffer serve --data-dir ./incidents --ingest-token demo

# terminal 2 — Python FastAPI app + fire one request
uv run uvicorn demo.fastapi-demo:app --port 8000
curl http://127.0.0.1:8000/reimburse    # returns 500 + captures an incident

# terminal 3 — Node example
INGEST_URL=http://127.0.0.1:8765/api/ingest INGEST_TOKEN=demo \
  node demo/node-demo.js
```

Refresh `http://127.0.0.1:8765` — both incidents appear in the list.
Click the FastAPI row: the timeline shows the *"Tariff lookup returned no
match"* debug event **before** the ERROR that triggered capture, and
**Download JSON** returns the original envelope byte-for-byte.

## Tests

```bash
uv sync --group dev
uv run pytest
```

## Security posture

- Never renders raw HTML from event fields.
- Ingest requires `Bearer <token>`.
- Optional viewer token; without it, bind to `127.0.0.1` and rely on a
  reverse proxy.
- Ingest rejects payloads over `INCIDENT_BUFFER_MAX_PAYLOAD` bytes.
- Data-dir traversal prevented on read + write.

## Status

v0.1 — working. Node package unpublished; Python not on PyPI yet.
