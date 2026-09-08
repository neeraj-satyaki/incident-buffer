"""Ring buffer + incident envelope + on-disk write.

ponytail: one file. collections.deque(maxlen=N) is the whole buffer.
No thread pool, no async queue. Add complexity only when a real prod trace
shows it.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from incident_buffer.version import SCHEMA_VERSION, __version__


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


@dataclass
class Event:
    ts_ms: int
    severity: Severity
    message: str
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts_ms": self.ts_ms,
            "severity": self.severity.value,
            "message": self.message,
            "fields": self.fields,
        }


@dataclass
class Incident:
    incident_id: str
    ts_ms: int
    trigger: str
    error_type: str | None
    error_message: str | None
    error_stack: str | None
    events: list[Event]
    evicted_count: int
    dropped_count: int
    truncated: bool


@dataclass
class IncidentEnvelope:
    schema_version: str
    incident_id: str
    ts_ms: int
    service: str
    service_version: str
    library_version: str
    host: str
    pid: int
    request_id: str | None
    job_id: str | None
    trace_id: str | None
    trigger: str
    error: dict | None
    evidence: dict
    capture_config: dict
    metadata: dict

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "incident_id": self.incident_id,
            "ts_ms": self.ts_ms,
            "service": self.service,
            "service_version": self.service_version,
            "library_version": self.library_version,
            "host": self.host,
            "pid": self.pid,
            "request_id": self.request_id,
            "job_id": self.job_id,
            "trace_id": self.trace_id,
            "trigger": self.trigger,
            "error": self.error,
            "evidence": self.evidence,
            "capture_config": self.capture_config,
            "metadata": self.metadata,
        }


class IncidentBuffer:
    """Bounded ring buffer + incident capture.

    Contract:
      - `.debug/.info/.warn/.error(msg, **fields)` appends events.
      - `.capture(reason=..., error=..., request_id=..., ...)` freezes the
        current buffer contents into an incident file.
      - Eviction is tracked: if events were dropped since the last capture,
        `evicted_count > 0` in the envelope.
    """

    def __init__(
        self,
        service: str,
        service_version: str,
        data_dir: str | Path,
        capacity: int = 500,
        max_field_bytes: int = 8_192,
        on_capture: Callable[[IncidentEnvelope, Path], None] | None = None,
    ) -> None:
        self.service = service
        self.service_version = service_version
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        self.max_field_bytes = max_field_bytes
        self.on_capture = on_capture

        self._buf: deque[Event] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._evicted = 0
        self._dropped = 0
        self._truncated = False
        self._host = socket.gethostname()
        self._pid = os.getpid()

    # ---- append ----
    def _log(self, severity: Severity, message: str, **fields: Any) -> None:
        raw = json.dumps(fields, default=str)
        truncated = False
        if len(raw) > self.max_field_bytes:
            truncated = True
            raw = raw[: self.max_field_bytes] + "…"
            fields = {"_raw_truncated": raw}
        ev = Event(ts_ms=int(time.time() * 1000), severity=severity,
                   message=message, fields=fields)
        with self._lock:
            if len(self._buf) == self._buf.maxlen:
                self._evicted += 1
            if truncated:
                self._truncated = True
            self._buf.append(ev)

    def debug(self, msg: str, **f: Any) -> None: self._log(Severity.DEBUG, msg, **f)
    def info(self, msg: str, **f: Any) -> None: self._log(Severity.INFO, msg, **f)
    def warn(self, msg: str, **f: Any) -> None: self._log(Severity.WARN, msg, **f)
    def error(self, msg: str, **f: Any) -> None: self._log(Severity.ERROR, msg, **f)

    # ---- capture ----
    def capture(
        self,
        *,
        trigger: str,
        error: BaseException | None = None,
        request_id: str | None = None,
        job_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> Path:
        with self._lock:
            events = list(self._buf)
            evicted = self._evicted
            dropped = self._dropped
            truncated = self._truncated
            self._buf.clear()
            self._evicted = 0
            self._dropped = 0
            self._truncated = False

        now_ms = int(time.time() * 1000)
        incident_id = f"inc_{uuid.uuid4().hex[:16]}"
        err_dict: dict | None = None
        if error is not None:
            err_dict = {
                "type": type(error).__name__,
                "message": str(error),
                "stack": "".join(traceback.format_exception(
                    type(error), error, error.__traceback__)),
            }

        env = IncidentEnvelope(
            schema_version=SCHEMA_VERSION,
            incident_id=incident_id,
            ts_ms=now_ms,
            service=self.service,
            service_version=self.service_version,
            library_version=__version__,
            host=self._host,
            pid=self._pid,
            request_id=request_id,
            job_id=job_id,
            trace_id=trace_id,
            trigger=trigger,
            error=err_dict,
            evidence={
                "events": [e.to_dict() for e in events],
                "event_count": len(events),
                "evicted_count": evicted,
                "dropped_count": dropped,
                "truncated": truncated,
                "capacity": self.capacity,
            },
            capture_config={
                "capacity": self.capacity,
                "max_field_bytes": self.max_field_bytes,
            },
            metadata=metadata or {},
        )
        path = self._write(env)
        if self.on_capture:
            try:
                self.on_capture(env, path)
            except Exception:  # noqa: BLE001
                # never let an exporter blow up the app path
                pass
        return path

    def _write(self, env: IncidentEnvelope) -> Path:
        d = self.data_dir / time.strftime("%Y/%m/%d", time.gmtime(env.ts_ms / 1000))
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{env.incident_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(env.to_dict(), indent=2))
        os.replace(tmp, path)   # atomic on posix; readers never see half a file
        return path

    # ---- helpers ----
    def snapshot(self) -> list[Event]:
        with self._lock:
            return list(self._buf)

    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._buf),
                "capacity": self.capacity,
                "evicted_since_last_capture": self._evicted,
                "dropped_since_last_capture": self._dropped,
                "truncated_flag": self._truncated,
            }
