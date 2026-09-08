"""Background HTTP exporter — bounded queue, retries, drop-on-overflow.

ponytail: threading + queue.Queue. Not a full retry framework. Real
outbound reliability = an actual queue (Kafka, SQS) — swap when needed.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Callable


class HttpExporter:
    def __init__(
        self,
        url: str,
        auth_token: str,
        max_queue: int = 200,
        max_retries: int = 3,
        timeout_s: float = 10.0,
    ) -> None:
        self.url = url
        self.auth_token = auth_token
        self.max_queue = max_queue
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.q: queue.Queue[Path] = queue.Queue(maxsize=max_queue)
        self.dropped_count = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def enqueue(self, path: Path) -> None:
        try:
            self.q.put_nowait(path)
        except queue.Full:
            self.dropped_count += 1  # overflow — bounded on purpose

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        try:
            import httpx
        except ImportError:
            return
        while not self._stop.is_set():
            try:
                path = self.q.get(timeout=1)
            except queue.Empty:
                continue
            self._send_with_retry(httpx, path)

    def _send_with_retry(self, httpx_mod, path: Path) -> None:
        body = path.read_text()
        backoff = 0.5
        for attempt in range(self.max_retries):
            try:
                r = httpx_mod.post(
                    self.url,
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {self.auth_token}",
                    },
                    timeout=self.timeout_s,
                )
                if r.status_code < 300:
                    return
                if r.status_code == 409:  # already ingested
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(backoff)
            backoff *= 2
        # give up — file stays on disk, next start can rescan
