// ponytail: single file, no framework. Fetch + DOM.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));

const state = {
  service: "", rangeS: 3600, q: "", live: true,
  incidents: [], cursorTs: null,
  selectedId: null, sel: null,
  eventFilter: {sev: "", q: ""},
  newQueue: [],
};

function fmtTs(ms) {
  const d = new Date(ms);
  return d.toISOString().replace("T", " ").replace("Z", " UTC");
}
function short(ms) {
  return new Date(ms).toISOString().substring(11, 19);
}

async function fetchIncidents(more = false) {
  const p = new URLSearchParams({ limit: "100" });
  if (state.service) p.set("service", state.service);
  if (state.rangeS > 0) p.set("since_ms", Date.now() - state.rangeS * 1000);
  if (state.q) p.set("q", state.q);
  if (more && state.cursorTs) p.set("cursor_ts", state.cursorTs);
  const r = await fetch("/api/incidents?" + p.toString());
  if (!r.ok) return;
  const j = await r.json();
  if (more) state.incidents = state.incidents.concat(j.incidents);
  else state.incidents = j.incidents;
  state.cursorTs = j.next_cursor_ts;
  renderList();
}

function renderList() {
  const rows = $("rows");
  rows.innerHTML = "";
  if (!state.incidents.length) { $("empty").hidden = false; $("more").hidden = true; return; }
  $("empty").hidden = true;
  for (const inc of state.incidents) {
    const tr = document.createElement("tr");
    tr.dataset.id = inc.incident_id;
    if (state.selectedId === inc.incident_id) tr.setAttribute("aria-selected", "true");
    const evText = `${inc.event_count} records`
      + (inc.evicted_count ? ` · ${inc.evicted_count} evicted` : "")
      + (inc.dropped_count ? ` · ${inc.dropped_count} dropped` : "")
      + (inc.truncated ? " · truncated" : "");
    const evCls = (inc.evicted_count || inc.dropped_count || inc.truncated) ? "warn" : "";
    tr.innerHTML = `
      <td class="mono">${short(inc.ts_ms)}</td>
      <td>${esc(inc.service)}</td>
      <td class="err">${esc(inc.error_type || "trigger")}<br><span class="muted">${esc(inc.error_message || inc.trigger)}</span></td>
      <td class="mono">${esc(inc.request_id || inc.job_id || inc.trace_id || "—")}</td>
      <td class="${evCls}">${esc(evText)}</td>`;
    tr.onclick = () => selectIncident(inc.incident_id);
    rows.appendChild(tr);
  }
  $("more").hidden = !state.cursorTs;
}

async function selectIncident(id) {
  state.selectedId = id;
  document.querySelectorAll("#rows tr").forEach(t =>
    t.setAttribute("aria-selected", String(t.dataset.id === id)));
  const r = await fetch(`/api/incidents/${encodeURIComponent(id)}`);
  if (!r.ok) return;
  state.sel = await r.json();
  renderDetail();
  if (window.matchMedia("(max-width:900px)").matches) {
    $("list-pane").classList.add("hidden-narrow");
    $("detail-pane").classList.remove("hidden-narrow");
    $("back").style.display = "inline-block";
  }
}

function renderDetail() {
  const { row, envelope: env } = state.sel;
  $("detail-pane").hidden = false;
  $("d-title").textContent = env.error?.type || env.trigger || row.incident_id;
  $("d-svc").textContent = env.service;
  $("d-ver").textContent = "v" + (env.service_version || "?");
  $("d-ts").textContent = fmtTs(env.ts_ms);
  $("d-req").textContent = env.request_id ? `req_id: ${env.request_id}` : "";
  $("d-job").textContent = env.job_id ? `job_id: ${env.job_id}` : "";
  $("d-trace").textContent = env.trace_id ? `trace_id: ${env.trace_id}` : "";
  $("download").href = `/api/incidents/${encodeURIComponent(env.incident_id)}/download`;
  $("copy-id").onclick = () => navigator.clipboard.writeText(env.incident_id);
  renderTimeline();
  renderException();
  renderMeta();
}

function renderTimeline() {
  const env = state.sel.envelope;
  const events = env.evidence.events || [];
  const ol = $("timeline"); ol.innerHTML = "";
  const banner = $("ev-banner");
  const bits = [];
  if (env.evidence.evicted_count)
    bits.push(`Earlier history is incomplete: ${env.evidence.evicted_count} records were evicted before capture.`);
  if (env.evidence.dropped_count)
    bits.push(`${env.evidence.dropped_count} records were dropped by the exporter.`);
  if (env.evidence.truncated) bits.push("Some field values were truncated.");
  banner.hidden = bits.length === 0;
  banner.textContent = bits.join(" ");

  const trigMs = env.ts_ms;
  const sevFilter = state.eventFilter.sev, qFilter = state.eventFilter.q.toLowerCase();
  events.forEach((e, i) => {
    if (sevFilter && e.severity !== sevFilter) return;
    if (qFilter && !JSON.stringify(e).toLowerCase().includes(qFilter)) return;
    const li = document.createElement("li");
    li.dataset.sev = e.severity;
    if (i === events.length - 1) li.classList.add("trigger");
    const rel = ((e.ts_ms - trigMs) / 1000).toFixed(3);
    li.innerHTML = `
      <span class="mono">${short(e.ts_ms)}</span>
      <span class="sev">[${esc(e.severity)}]</span>
      ${esc(e.message)}
      <span class="rel">t${rel >= 0 ? "+" : ""}${rel}s</span>
      <button class="copy-ev" data-i="${i}">copy JSON</button>
      ${Object.keys(e.fields || {}).length
        ? `<details><summary>fields</summary><pre>${esc(JSON.stringify(e.fields, null, 2))}</pre></details>` : ""}`;
    ol.appendChild(li);
  });
  ol.querySelectorAll(".copy-ev").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      const i = Number(b.dataset.i);
      navigator.clipboard.writeText(JSON.stringify(events[i], null, 2));
    };
  });
}

function renderException() {
  const env = state.sel.envelope;
  const err = env.error;
  if (!err) { $("ex-type").textContent = "(no exception captured)";
              $("ex-msg").textContent = ""; $("ex-stack").textContent = ""; return; }
  $("ex-type").textContent = err.type;
  $("ex-msg").textContent = err.message || "";
  $("ex-stack").textContent = err.stack || "";
  $("copy-stack").onclick = () => navigator.clipboard.writeText(err.stack || "");
}

function renderMeta() {
  const { row, envelope: env } = state.sel;
  const dl = $("meta"); dl.innerHTML = "";
  const rows = [
    ["Incident ID", env.incident_id],
    ["Trigger", env.trigger],
    ["Service", `${env.service} v${env.service_version || "?"}`],
    ["Host / PID", `${env.host || "?"} / ${env.pid || "?"}`],
    ["Correlation", [env.request_id, env.job_id, env.trace_id].filter(Boolean).join(" · ") || "—"],
    ["Buffer capacity", env.capture_config?.capacity ?? "?"],
    ["Events captured", env.evidence.event_count],
    ["Evicted", env.evidence.evicted_count],
    ["Dropped", env.evidence.dropped_count],
    ["Truncated", env.evidence.truncated ? "yes" : "no"],
    ["File", row.file_path],
    ["Library", env.library_version || "?"],
    ["Schema", env.schema_version || "?"],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    dl.appendChild(dt); dl.appendChild(dd);
  }
}

// ---- SSE ----
let es = null;
function connect() {
  if (es) es.close();
  es = new EventSource("/api/events");
  $("conn").textContent = "connecting…"; $("conn").className = "conn";
  es.addEventListener("hello", () => {
    $("conn").textContent = "connected to dashboard"; $("conn").className = "conn live";
    fetchServices(); fetchIncidents();
  });
  es.addEventListener("incident.new", (m) => {
    if (!state.live) return;
    const d = JSON.parse(m.data);
    state.newQueue.push(d);
    $("new-arrivals").hidden = false;
    $("new-arrivals").textContent = `${state.newQueue.length} new incidents · click to refresh`;
    $("updated").textContent = "updated " + short(Date.now());
  });
  es.onerror = () => {
    $("conn").textContent = "connection lost — retrying"; $("conn").className = "conn err";
    setTimeout(connect, 3000);
  };
}

async function fetchServices() {
  const r = await fetch("/api/health");
  if (!r.ok) return;
  const h = await r.json();
  const sel = $("svc");
  const cur = sel.value;
  sel.innerHTML = `<option value="">All services</option>` +
    h.services.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
  sel.value = cur;
}

// ---- bindings ----
$("svc").onchange = (e) => { state.service = e.target.value; fetchIncidents(); };
$("range").onchange = (e) => { state.rangeS = Number(e.target.value); fetchIncidents(); };
$("q").addEventListener("input", (e) => {
  state.q = e.target.value;
  clearTimeout(window._sd);
  window._sd = setTimeout(fetchIncidents, 250);
});
$("live").onchange = (e) => { state.live = e.target.checked; };
$("more").onclick = () => fetchIncidents(true);
$("new-arrivals").onclick = () => {
  state.newQueue = []; $("new-arrivals").hidden = true; fetchIncidents();
};
$("back").onclick = () => {
  $("list-pane").classList.remove("hidden-narrow");
  $("detail-pane").classList.add("hidden-narrow");
  $("back").style.display = "none";
};
document.querySelectorAll(".tab").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === b));
    document.querySelectorAll(".tab-body").forEach((tb) => tb.hidden = true);
    $("tab-" + b.dataset.tab).hidden = false;
  };
});
$("ev-sev").onchange = (e) => { state.eventFilter.sev = e.target.value; renderTimeline(); };
$("ev-q").oninput = (e) => { state.eventFilter.q = e.target.value; renderTimeline(); };

connect();
