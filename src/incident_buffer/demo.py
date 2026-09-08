"""Synthetic demo incidents — clearly labelled DEMO in the payload."""
from __future__ import annotations

from pathlib import Path

from incident_buffer.buffer import IncidentBuffer


def run_demo(data_dir: Path) -> int:
    ib = IncidentBuffer(
        service="reimbursement-api",
        service_version="1.4.0-DEMO",
        data_dir=data_dir,
        capacity=200,
    )
    ib.debug("Session loaded", session_id="s_ab12", user_id="u_9001")
    ib.debug("Meter reading accepted", reading_kwh=42.7, meter="M-2211")
    ib.debug("Tariff lookup returned no match", region="MH", plan="TOU-A",
             _demo=True)
    try:
        raise ValueError("No matching tariff for region=MH plan=TOU-A")
    except ValueError as e:
        p = ib.capture(
            trigger="fastapi_exception",
            error=e,
            request_id="req_abc123",
            trace_id="trace_" + "d" * 12,
            metadata={"demo": True, "note": "DEMO INCIDENT — NOT PRODUCTION"},
        )
        print(f"[demo] wrote {p}")

    ib2 = IncidentBuffer(
        service="batch-worker",
        service_version="0.9.2-DEMO",
        data_dir=data_dir,
        capacity=100,
    )
    for i in range(120):  # deliberately overflow — creates evicted_count
        ib2.debug(f"job step {i}", i=i, _demo=True)
    ib2.warn("retry backoff", attempt=3, _demo=True)
    try:
        raise TimeoutError("downstream billing service timed out after 15s")
    except TimeoutError as e:
        p = ib2.capture(
            trigger="worker_timeout",
            error=e,
            job_id="job_9f22",
            metadata={"demo": True},
        )
        print(f"[demo] wrote {p}")
    return 0
