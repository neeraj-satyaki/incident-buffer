"""FastAPI dashboard app.

ponytail: one file wires routes + SSE. Bounded queues. No background jobs
beyond a file-watcher thread. Static assets shipped inside the wheel.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from incident_buffer.dashboard.index import Index
from incident_buffer.version import SCHEMA_VERSION, __version__

DATA_DIR = Path(os.environ.get("INCIDENT_BUFFER_DATA_DIR", "./incidents")).resolve()
INGEST_TOKEN = os.environ.get("INCIDENT_BUFFER_INGEST_TOKEN", "")
VIEWER_TOKEN = os.environ.get("INCIDENT_BUFFER_VIEWER_TOKEN", "")
MAX_PAYLOAD_BYTES = int(os.environ.get("INCIDENT_BUFFER_MAX_PAYLOAD", 1_048_576))
STATIC_DIR = Path(__file__).parent.parent / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
idx = Index(DATA_DIR / "_index.sqlite")
idx.rescan(DATA_DIR)

CORS_ORIGINS = os.environ.get("INCIDENT_BUFFER_CORS_ORIGINS", "*").split(",")

app = FastAPI(title="Incident Buffer Dashboard", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["authorization", "content-type"],
    allow_credentials=False,
    max_age=600,
)


def _rescan_loop() -> None:
    """Background rescan for files that landed on disk without going through
    /api/ingest (local capture from a co-hosted app, network partition, etc.)."""
    import threading
    def _tick():
        while True:
            try:
                idx.rescan(DATA_DIR)
            except Exception:  # noqa: BLE001
                pass
            threading.Event().wait(15.0)
    t = threading.Thread(target=_tick, daemon=True, name="ib-rescan")
    t.start()


_rescan_loop()


# ---- SSE broadcaster --------------------------------------------------------
class Broadcaster:
    def __init__(self, max_subscribers: int = 64, subscriber_queue: int = 128) -> None:
        self._subs: set[Queue] = set()
        self._lock = asyncio.Lock() if False else None
        self.max_subscribers = max_subscribers
        self.subscriber_queue = subscriber_queue

    def subscribe(self) -> Queue:
        q: Queue = Queue(maxsize=self.subscriber_queue)
        if len(self._subs) >= self.max_subscribers:
            oldest = next(iter(self._subs))
            self._subs.discard(oldest)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        self._subs.discard(q)

    def broadcast(self, ev: dict) -> None:
        dead = []
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except Exception:  # noqa: BLE001
                dead.append(q)
        for q in dead:
            self._subs.discard(q)


bc = Broadcaster()


# ---- helpers ----------------------------------------------------------------
def _auth_viewer(req: Request) -> None:
    if not VIEWER_TOKEN:
        return
    hdr = req.headers.get("authorization", "")
    if hdr != f"Bearer {VIEWER_TOKEN}":
        raise HTTPException(status_code=401, detail="viewer auth required")


def _auth_ingest(hdr: str | None) -> None:
    if not INGEST_TOKEN:
        raise HTTPException(status_code=503, detail="ingest disabled — set INCIDENT_BUFFER_INGEST_TOKEN")
    if hdr != f"Bearer {INGEST_TOKEN}":
        raise HTTPException(status_code=401, detail="bad ingest token")


def _safe_within(p: Path, base: Path) -> Path:
    r = p.resolve()
    if not str(r).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="path outside data dir")
    return r


def _write_atomic(path: Path, body: bytes) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(body)
    os.replace(tmp, path)


# ---- routes -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def root(req: Request) -> HTMLResponse:
    _auth_viewer(req)
    html = (STATIC_DIR / "index.html").read_text()
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "data_dir": str(DATA_DIR),
        "services": idx.services(),
        "note": "dashboard reachability != application health",
    }


@app.get("/api/incidents")
def list_incidents(req: Request,
                   service: Optional[str] = None,
                   since_ms: Optional[int] = None,
                   until_ms: Optional[int] = None,
                   q: Optional[str] = None,
                   limit: int = Query(default=100, ge=1, le=500),
                   cursor_ts: Optional[int] = None) -> dict:
    _auth_viewer(req)
    rows = idx.list(service=service, since_ms=since_ms, until_ms=until_ms,
                    q=q, limit=limit, cursor_ts=cursor_ts)
    next_cursor = rows[-1]["ts_ms"] if len(rows) == limit else None
    return {"incidents": rows, "next_cursor_ts": next_cursor}


@app.get("/api/incidents/{incident_id}")
def get_incident(req: Request, incident_id: str) -> dict:
    _auth_viewer(req)
    row = idx.get(incident_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    path = _safe_within(Path(row["file_path"]), DATA_DIR)
    try:
        env = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=500, detail="incident file unreadable")
    return {"row": row, "envelope": env}


@app.get("/api/incidents/{incident_id}/download")
def download_incident(req: Request, incident_id: str) -> FileResponse:
    _auth_viewer(req)
    row = idx.get(incident_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    path = _safe_within(Path(row["file_path"]), DATA_DIR)
    return FileResponse(str(path), media_type="application/json",
                        filename=f"{incident_id}.json")


class IngestReq(BaseModel):
    schema_version: str
    incident_id: str
    ts_ms: int
    service: str
    service_version: str | None = None
    library_version: str | None = None
    host: str | None = None
    pid: int | None = None
    request_id: str | None = None
    job_id: str | None = None
    trace_id: str | None = None
    trigger: str
    error: dict | None = None
    evidence: dict
    capture_config: dict | None = None
    metadata: dict | None = None


@app.post("/api/ingest")
async def ingest(req: Request,
                 authorization: Optional[str] = Header(default=None)) -> JSONResponse:
    _auth_ingest(authorization)
    raw = await req.body()
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        env = IngestReq.model_validate_json(raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"bad envelope: {e}")
    if env.schema_version != SCHEMA_VERSION:
        raise HTTPException(status_code=400, detail=f"unsupported schema {env.schema_version}")

    d = DATA_DIR / time.strftime("%Y/%m/%d", time.gmtime(env.ts_ms / 1000))
    d.mkdir(parents=True, exist_ok=True)
    path = _safe_within(d / f"{env.incident_id}.json", DATA_DIR)

    env_dict = json.loads(raw)
    if path.exists():
        # duplicate retry — verify by content hash; conflict if body differs
        existing = path.read_bytes()
        if hashlib.sha256(existing).hexdigest() != hashlib.sha256(raw).hexdigest():
            raise HTTPException(status_code=409, detail="incident_id conflict")
        idx.upsert_from_envelope(env_dict, path)  # ensure index catches up
        bc.broadcast({"type": "incident.new", "incident_id": env.incident_id, "ts_ms": env.ts_ms})
        return JSONResponse({"ok": True, "already_present": True}, status_code=200)

    _write_atomic(path, raw)
    idx.upsert_from_envelope(env_dict, path)
    bc.broadcast({"type": "incident.new", "incident_id": env.incident_id, "ts_ms": env.ts_ms})
    return JSONResponse({"ok": True, "incident_id": env.incident_id}, status_code=201)


@app.get("/api/events")
async def events(req: Request):
    _auth_viewer(req)
    q = bc.subscribe()

    async def gen():
        # first: refresh hint so reconnecting clients re-pull the list
        yield "event: hello\ndata: {\"refresh\":true}\n\n"
        try:
            while True:
                if await req.is_disconnected():
                    return
                try:
                    ev = q.get(timeout=1)
                except Empty:
                    # keepalive
                    yield ": ping\n\n"
                    continue
                yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
        finally:
            bc.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
