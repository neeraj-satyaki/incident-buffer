from __future__ import annotations

import json
from pathlib import Path

from incident_buffer import IncidentBuffer


def test_capture_writes_envelope(tmp_path: Path) -> None:
    ib = IncidentBuffer(service="svc", service_version="1", data_dir=tmp_path, capacity=8)
    ib.debug("a"); ib.debug("b"); ib.info("c")
    try:
        raise ValueError("boom")
    except ValueError as e:
        path = ib.capture(trigger="test", error=e, request_id="r1")
    env = json.loads(path.read_text())
    assert env["service"] == "svc"
    assert env["request_id"] == "r1"
    assert env["error"]["type"] == "ValueError"
    assert env["evidence"]["event_count"] == 3
    assert env["evidence"]["evicted_count"] == 0


def test_eviction_counted(tmp_path: Path) -> None:
    ib = IncidentBuffer(service="svc", service_version="1", data_dir=tmp_path, capacity=4)
    for i in range(10):
        ib.debug(f"e{i}", i=i)
    p = ib.capture(trigger="test")
    env = json.loads(p.read_text())
    assert env["evidence"]["event_count"] == 4
    assert env["evidence"]["evicted_count"] == 6


def test_truncation(tmp_path: Path) -> None:
    ib = IncidentBuffer(service="svc", service_version="1", data_dir=tmp_path,
                        capacity=4, max_field_bytes=32)
    ib.debug("big", blob="x" * 500)
    p = ib.capture(trigger="test")
    env = json.loads(p.read_text())
    assert env["evidence"]["truncated"] is True
