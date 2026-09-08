/**
 * Browser client — same envelope schema, ships over fetch to /api/ingest.
 *
 * Usage:
 *   <script src="https://dash.internal/static/incident-buffer.js"></script>
 *   <script>
 *     const ib = new IncidentBuffer({
 *       ingestUrl: "https://dash.internal/api/ingest",
 *       ingestToken: "…",           // from your CSP-safe config endpoint
 *       service: "web-frontend",
 *       serviceVersion: "3.0.5",
 *       capacity: 300,
 *     });
 *     ib.installGlobalHandlers();  // window.onerror + unhandledrejection
 *     ib.debug("cart loaded", { items: 3 });
 *   </script>
 *
 * ponytail: single file, no framework, no build. Sends when it hits an
 * error; buffers the last N events in the tab. No polling.
 */
(function (root) {
  "use strict";

  const SCHEMA = "1", LIB = "0.1.0";

  function IncidentBuffer(opts) {
    if (!opts || !opts.ingestUrl || !opts.service) {
      throw new Error("IncidentBuffer: need { ingestUrl, service, ingestToken }");
    }
    this.ingestUrl = opts.ingestUrl;
    this.ingestToken = opts.ingestToken || "";
    this.service = opts.service;
    this.serviceVersion = opts.serviceVersion || "0.0.0";
    this.capacity = opts.capacity || 200;
    this.maxFieldBytes = opts.maxFieldBytes || 4096;
    this.buf = [];
    this.evicted = 0;
    this.truncated = false;
  }

  function _uid() {
    return "inc_" + (crypto.getRandomValues(new Uint8Array(8))
      || new Uint8Array(8))
      .reduce((s, b) => s + b.toString(16).padStart(2, "0"), "");
  }

  IncidentBuffer.prototype._push = function (severity, message, fields) {
    let f = fields || {};
    let raw;
    try { raw = JSON.stringify(f); } catch (_) { raw = "\"[unserializable]\""; }
    if (raw.length > this.maxFieldBytes) {
      f = { _raw_truncated: raw.slice(0, this.maxFieldBytes) + "…" };
      this.truncated = true;
    }
    if (this.buf.length >= this.capacity) { this.buf.shift(); this.evicted++; }
    this.buf.push({ ts_ms: Date.now(), severity, message: String(message ?? ""), fields: f });
  };

  ["debug", "info", "warn", "error"].forEach(function (lvl) {
    IncidentBuffer.prototype[lvl] = function (m, f) {
      this._push(lvl.toUpperCase(), m, f);
    };
  });

  IncidentBuffer.prototype.capture = function (opts) {
    opts = opts || {};
    const now = Date.now();
    const events = this.buf.slice();
    const evicted = this.evicted, truncated = this.truncated;
    this.buf = []; this.evicted = 0; this.truncated = false;

    const err = opts.error;
    const errObj = err ? {
      type: err.name || "Error",
      message: String(err.message || err),
      stack: String(err.stack || ""),
    } : null;

    const env = {
      schema_version: SCHEMA,
      incident_id: _uid(),
      ts_ms: now,
      service: this.service,
      service_version: this.serviceVersion,
      library_version: LIB,
      host: (typeof location !== "undefined" ? location.hostname : "") || "browser",
      pid: 0,
      request_id: opts.requestId || null,
      job_id: opts.jobId || null,
      trace_id: opts.traceId || null,
      trigger: opts.trigger || "browser_error",
      error: errObj,
      evidence: {
        events, event_count: events.length,
        evicted_count: evicted, dropped_count: 0,
        truncated, capacity: this.capacity,
      },
      capture_config: { capacity: this.capacity, max_field_bytes: this.maxFieldBytes },
      metadata: opts.metadata || {
        ua: (typeof navigator !== "undefined" ? navigator.userAgent : ""),
        url: (typeof location !== "undefined" ? location.href : ""),
      },
    };

    const body = JSON.stringify(env);
    // sendBeacon works during page unload; falls back to fetch keepalive.
    try {
      if (navigator && navigator.sendBeacon) {
        const blob = new Blob([body], { type: "application/json" });
        // sendBeacon can't set Authorization header — put token in query fallback
        // but preferred: use fetch keepalive with header.
        // We attempt fetch first; sendBeacon is the unload backup.
      }
    } catch (_) { /* ignore */ }

    fetch(this.ingestUrl, {
      method: "POST",
      body,
      headers: {
        "content-type": "application/json",
        "authorization": "Bearer " + this.ingestToken,
      },
      keepalive: true,
      mode: "cors",
      credentials: "omit",
    }).catch(() => { /* offline / blocked — accept the loss, ring buffer is bounded */ });

    return env.incident_id;
  };

  IncidentBuffer.prototype.installGlobalHandlers = function () {
    const self = this;
    window.addEventListener("error", function (e) {
      self.capture({ trigger: "window_onerror", error: e.error || new Error(e.message) });
    });
    window.addEventListener("unhandledrejection", function (e) {
      const reason = e.reason;
      self.capture({
        trigger: "unhandled_rejection",
        error: reason instanceof Error ? reason : new Error(String(reason)),
      });
    });
    // wrap console.error too, so anything shouted also lands in the buffer
    const _e = console.error;
    console.error = function () {
      try { self.error(Array.prototype.map.call(arguments, String).join(" ")); } catch (_) {}
      _e.apply(console, arguments);
    };
  };

  // expose
  root.IncidentBuffer = IncidentBuffer;
})(typeof window !== "undefined" ? window : globalThis);
