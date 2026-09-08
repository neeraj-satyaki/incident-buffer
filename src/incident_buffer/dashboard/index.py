"""SQLite index for incidents. Rebuilds from files on start.

ponytail: one table, one file. Fine to 100k incidents.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  incident_id TEXT PRIMARY KEY,
  schema_version TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  service TEXT NOT NULL,
  service_version TEXT,
  request_id TEXT,
  job_id TEXT,
  trace_id TEXT,
  error_type TEXT,
  error_message TEXT,
  event_count INTEGER NOT NULL DEFAULT 0,
  evicted_count INTEGER NOT NULL DEFAULT 0,
  dropped_count INTEGER NOT NULL DEFAULT 0,
  truncated INTEGER NOT NULL DEFAULT 0,
  trigger TEXT NOT NULL,
  file_path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ts   ON incidents(ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_svc  ON incidents(service, ts_ms DESC);
CREATE INDEX IF NOT EXISTS idx_req  ON incidents(request_id);
CREATE INDEX IF NOT EXISTS idx_trace ON incidents(trace_id);
"""


class Index:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def upsert_from_envelope(self, envelope: dict, file_path: Path) -> None:
        row = self._flatten(envelope, file_path)
        with self._lock, self._conn() as c:
            c.execute("""
              INSERT INTO incidents(
                incident_id, schema_version, ts_ms, service, service_version,
                request_id, job_id, trace_id, error_type, error_message,
                event_count, evicted_count, dropped_count, truncated,
                trigger, file_path)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(incident_id) DO UPDATE SET
                file_path=excluded.file_path,
                event_count=excluded.event_count,
                evicted_count=excluded.evicted_count,
                dropped_count=excluded.dropped_count,
                truncated=excluded.truncated
            """, row)

    @staticmethod
    def _flatten(env: dict, file_path: Path) -> tuple:
        err = env.get("error") or {}
        ev = env.get("evidence") or {}
        return (
            env["incident_id"], env.get("schema_version", "1"), env["ts_ms"],
            env["service"], env.get("service_version"),
            env.get("request_id"), env.get("job_id"), env.get("trace_id"),
            err.get("type"), err.get("message"),
            int(ev.get("event_count", 0)), int(ev.get("evicted_count", 0)),
            int(ev.get("dropped_count", 0)), int(bool(ev.get("truncated", False))),
            env.get("trigger", "unknown"),
            str(file_path),
        )

    def list(self, *, service: str | None = None, since_ms: int | None = None,
             until_ms: int | None = None, q: str | None = None,
             limit: int = 100, cursor_ts: int | None = None) -> list[dict]:
        where = ["1=1"]
        args: list = []
        if service:
            where.append("service = ?"); args.append(service)
        if since_ms:
            where.append("ts_ms >= ?"); args.append(since_ms)
        if until_ms:
            where.append("ts_ms <= ?"); args.append(until_ms)
        if q:
            where.append("(incident_id LIKE ? OR request_id LIKE ? OR job_id LIKE ? "
                         "OR trace_id LIKE ? OR error_message LIKE ?)")
            like = f"%{q}%"
            args.extend([like, like, like, like, like])
        if cursor_ts:
            where.append("ts_ms < ?"); args.append(cursor_ts)
        args.append(min(int(limit), 500))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM incidents WHERE {' AND '.join(where)} "
                f"ORDER BY ts_ms DESC LIMIT ?", args).fetchall()
        return [dict(r) for r in rows]

    def get(self, incident_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM incidents WHERE incident_id=?",
                          (incident_id,)).fetchone()
        return dict(r) if r else None

    def services(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT service, COUNT(*) c FROM incidents GROUP BY service "
                "ORDER BY c DESC").fetchall()
        return [r["service"] for r in rows]

    def rescan(self, data_dir: Path) -> int:
        """Bounded directory scan — up to 50k files."""
        n = 0
        for p in sorted(data_dir.rglob("*.json"))[:50_000]:
            if not p.is_file() or p.name.endswith(".tmp"):
                continue
            try:
                env = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if "incident_id" not in env:
                continue
            self.upsert_from_envelope(env, p)
            n += 1
        return n
