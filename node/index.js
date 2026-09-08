/**
 * @satyaki/incident-buffer — Node / pino companion.
 *
 * Wire as a pino stream + call `.capture()` on error:
 *
 *   const pino = require('pino');
 *   const { IncidentBuffer } = require('@satyaki/incident-buffer');
 *   const ib = new IncidentBuffer({
 *     service: 'checkout', serviceVersion: '2.1.0',
 *     dataDir: './incidents', capacity: 500,
 *   });
 *   const log = pino({ level: 'debug' }, ib.stream());
 *   try { doStuff(); }
 *   catch (err) { ib.capture({ trigger: 'unhandled', error: err }); throw err; }
 *
 * Ponytail: single file, no deps besides node stdlib. Compatible with the
 * Python dashboard's ingest endpoint.
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { Writable } = require('stream');

const SCHEMA_VERSION = '1';
const LIB_VERSION = '0.1.0';
const SEV_MAP = {10:'DEBUG', 20:'DEBUG', 30:'INFO', 40:'WARN', 50:'ERROR', 60:'FATAL'};

class IncidentBuffer {
  constructor({service, serviceVersion, dataDir, capacity = 500, maxFieldBytes = 8192,
               onCapture = null, exporter = null}) {
    if (!service) throw new Error('service required');
    this.service = service;
    this.serviceVersion = serviceVersion || '0.0.0';
    this.dataDir = path.resolve(dataDir);
    fs.mkdirSync(this.dataDir, { recursive: true });
    this.capacity = capacity;
    this.maxFieldBytes = maxFieldBytes;
    this.onCapture = onCapture;
    this.exporter = exporter;
    this._buf = [];
    this._evicted = 0;
    this._dropped = 0;
    this._truncated = false;
    this._host = os.hostname();
    this._pid = process.pid;
  }

  _push(sev, message, fields) {
    const ev = { ts_ms: Date.now(), severity: sev, message: String(message ?? ''), fields: fields || {} };
    const raw = JSON.stringify(ev.fields);
    if (raw.length > this.maxFieldBytes) {
      ev.fields = { _raw_truncated: raw.slice(0, this.maxFieldBytes) + '…' };
      this._truncated = true;
    }
    if (this._buf.length >= this.capacity) {
      this._buf.shift();
      this._evicted++;
    }
    this._buf.push(ev);
  }

  debug(m, f) { this._push('DEBUG', m, f); }
  info(m, f)  { this._push('INFO', m, f); }
  warn(m, f)  { this._push('WARN', m, f); }
  error(m, f) { this._push('ERROR', m, f); }

  /** Pino writable-stream sink. */
  stream() {
    const self = this;
    return new Writable({
      write(chunk, _enc, cb) {
        try {
          const obj = JSON.parse(chunk.toString());
          const sev = SEV_MAP[obj.level] || 'INFO';
          const { level, time, hostname, pid, msg, ...rest } = obj;
          self._push(sev, msg, rest);
        } catch (_) { /* non-JSON line: ignore */ }
        cb();
      },
    });
  }

  capture({trigger, error, requestId, jobId, traceId, metadata} = {}) {
    if (!trigger) throw new Error('trigger required');
    const now = Date.now();
    const events = this._buf.slice();
    const evicted = this._evicted, dropped = this._dropped, truncated = this._truncated;
    this._buf = []; this._evicted = 0; this._dropped = 0; this._truncated = false;

    const err = error ? {
      type: error.name || 'Error',
      message: String(error.message || error),
      stack: String(error.stack || ''),
    } : null;

    const incidentId = 'inc_' + crypto.randomBytes(8).toString('hex');
    const env = {
      schema_version: SCHEMA_VERSION,
      incident_id: incidentId,
      ts_ms: now,
      service: this.service,
      service_version: this.serviceVersion,
      library_version: LIB_VERSION,
      host: this._host,
      pid: this._pid,
      request_id: requestId || null,
      job_id: jobId || null,
      trace_id: traceId || null,
      trigger,
      error: err,
      evidence: {
        events,
        event_count: events.length,
        evicted_count: evicted,
        dropped_count: dropped,
        truncated,
        capacity: this.capacity,
      },
      capture_config: { capacity: this.capacity, max_field_bytes: this.maxFieldBytes },
      metadata: metadata || {},
    };

    const d = path.join(this.dataDir,
      new Date(now).toISOString().slice(0, 10).replaceAll('-', '/'));
    fs.mkdirSync(d, { recursive: true });
    const p = path.join(d, incidentId + '.json');
    const tmp = p + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(env, null, 2));
    fs.renameSync(tmp, p);

    if (this.onCapture) { try { this.onCapture(env, p); } catch (_) {} }
    if (this.exporter) { try { this.exporter.enqueue(p, env); } catch (_) {} }
    return p;
  }
}

/** Bounded background HTTP exporter — POST envelopes to the dashboard. */
class HttpExporter {
  constructor({url, authToken, maxQueue = 200, maxRetries = 3, timeoutMs = 10_000}) {
    this.url = url; this.authToken = authToken;
    this.maxQueue = maxQueue; this.maxRetries = maxRetries; this.timeoutMs = timeoutMs;
    this.q = []; this.dropped = 0; this._busy = false;
  }
  enqueue(p, envelope) {
    if (this.q.length >= this.maxQueue) { this.dropped++; return; }
    this.q.push(envelope);
    this._drain();
  }
  async _drain() {
    if (this._busy) return; this._busy = true;
    while (this.q.length) {
      const env = this.q.shift();
      await this._send(env);
    }
    this._busy = false;
  }
  async _send(env) {
    let backoff = 500;
    for (let i = 0; i < this.maxRetries; i++) {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), this.timeoutMs);
      try {
        const r = await fetch(this.url, {
          method: 'POST',
          body: JSON.stringify(env),
          headers: {
            'content-type': 'application/json',
            'authorization': 'Bearer ' + this.authToken,
          },
          signal: ctl.signal,
        });
        clearTimeout(t);
        if (r.status < 300 || r.status === 409) return;
      } catch (_) { clearTimeout(t); }
      await new Promise((r) => setTimeout(r, backoff));
      backoff *= 2;
    }
  }
}

module.exports = { IncidentBuffer, HttpExporter, SCHEMA_VERSION, LIB_VERSION };
