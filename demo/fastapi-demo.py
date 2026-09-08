"""FastAPI demo app that writes an incident when /reimburse fails.

Run:
  uvicorn demo.fastapi-demo:app --port 8000
Then:
  curl http://127.0.0.1:8000/reimburse

Check the dashboard at http://127.0.0.1:8765
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException, Request
from incident_buffer import IncidentBuffer

ib = IncidentBuffer(
    service="reimbursement-api",
    service_version="1.4.0-DEMO",
    data_dir="./incidents",
    capacity=500,
)
app = FastAPI()


@app.middleware("http")
async def add_req_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
    request.state.req_id = rid
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
def reimburse(request: Request) -> dict:
    ib.debug("Session loaded", session_id="s_ab12")
    ib.debug("Meter reading accepted", reading_kwh=42.7)
    ib.debug("Tariff lookup returned no match", region="MH", plan="TOU-A")
    raise HTTPException(status_code=500,
                        detail="No matching tariff for region=MH plan=TOU-A")
