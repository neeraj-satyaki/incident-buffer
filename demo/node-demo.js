/**
 * Node demo — writes one incident locally, then optionally POSTs it to the
 * dashboard (INGEST_URL + INGEST_TOKEN env vars).
 *
 * Run:
 *   node demo/node-demo.js
 *   # or with dashboard ingest:
 *   INGEST_URL=http://127.0.0.1:8765/api/ingest INGEST_TOKEN=demo \
 *     node demo/node-demo.js
 */
'use strict';
const path = require('path');
const { IncidentBuffer, HttpExporter } = require('../node');

const dataDir = path.resolve('./incidents');
const exporter = process.env.INGEST_URL
  ? new HttpExporter({ url: process.env.INGEST_URL, authToken: process.env.INGEST_TOKEN || '' })
  : null;

const ib = new IncidentBuffer({
  service: 'checkout-node',
  serviceVersion: '2.1.0-DEMO',
  dataDir,
  capacity: 300,
  exporter,
});

ib.debug('cart loaded', { cart_id: 'c_991', items: 3, _demo: true });
ib.debug('discount applied', { code: 'FALL10', _demo: true });
ib.warn('payment provider slow', { latency_ms: 2100, _demo: true });

try {
  throw new Error('payment_intent_capture_failed: card_declined');
} catch (err) {
  const p = ib.capture({
    trigger: 'unhandled_exception',
    error: err,
    requestId: 'req_node_' + Math.random().toString(16).slice(2, 10),
    metadata: { demo: true, note: 'DEMO INCIDENT — NOT PRODUCTION' },
  });
  console.log('[node-demo] wrote', p);
  if (exporter) console.log('[node-demo] queued for ingest at', process.env.INGEST_URL);
}
