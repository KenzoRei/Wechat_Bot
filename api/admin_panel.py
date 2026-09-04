"""
Admin/dev panel — a single self-contained HTML page served directly by the
app (no separate hosting, no build step, no framework).

Deliberately NOT behind verify_admin_key at the route level — a browser has
to be able to load the page itself before it has a key to send. The page's
own JS prompts for the admin key once, stores it in sessionStorage, and
sends it as X-Admin-Key on every call to the already-protected /admin/*
endpoints (same auth as PowerShell/curl usage — this page has no elevated
access of its own, it's just a thin client over the existing admin API).

Mostly read-only, with one write action: assigning a role to a Kefu staff
member (PATCH /admin/kefu-staff/{staff_id}) — the most urgent operational
gap this panel exists to close, since Kefu self-registration leaves new
staff stuck in the "pending" role until an admin promotes them.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/admin/panel", response_class=HTMLResponse)
def admin_panel() -> HTMLResponse:
    return HTMLResponse(content=_PANEL_HTML)


_PANEL_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeChat Bot — Admin Panel</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #0b0d12;
    --card: #161a22;
    --border: #2a2f3a;
    --text: #e6e8eb;
    --muted: #8b93a1;
    --accent: #5b9dff;
    --ok: #3ddc84;
    --bad: #ff5c5c;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f5f6f8;
      --card: #ffffff;
      --border: #dde1e7;
      --text: #1a1d23;
      --muted: #626a78;
      --accent: #2563eb;
      --ok: #16a34a;
      --bad: #dc2626;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 24px;
    max-width: 980px;
    margin: 0 auto;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
  .stat {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 16px;
    min-width: 120px;
  }
  .stat .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .stat .value { font-size: 20px; font-weight: 600; margin-top: 2px; }
  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge.ok { background: color-mix(in srgb, var(--ok) 18%, transparent); color: var(--ok); }
  .badge.bad { background: color-mix(in srgb, var(--bad) 18%, transparent); color: var(--bad); }
  .badge.muted { background: color-mix(in srgb, var(--muted) 18%, transparent); color: var(--muted); }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 14px;
  }
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
  }
  .card-header h3 { margin: 0; font-size: 15px; }
  .card-header .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
  td.actions { display: flex; gap: 6px; align-items: center; white-space: nowrap; }
  td.actions button { padding: 6px 10px; }
  th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .03em; }
  tr:last-child td { border-bottom: none; }
  button {
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }
  button.secondary {
    background: transparent;
    color: var(--text);
    border: 1px solid var(--border);
  }
  input[type=password] {
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    width: 260px;
  }
  .toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 20px; }
  .empty { color: var(--muted); font-size: 13px; padding: 8px 0; }
  .error { color: var(--bad); font-size: 13px; margin-top: 8px; }
  #gate { display: flex; flex-direction: column; gap: 10px; max-width: 340px; margin-top: 60px; }
  #app { display: none; }
  .tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }
  .tab-btn {
    background: transparent;
    color: var(--muted);
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 10px 14px;
    font-weight: 600;
  }
  .tab-btn.active { color: var(--text); border-bottom-color: var(--accent); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  .warehouse-checks { display: flex; gap: 8px; flex-wrap: wrap; }
  .warehouse-checks label { display: inline-flex; align-items: center; gap: 3px; font-size: 12px; white-space: nowrap; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
  .filters select, .filters input[type=date] {
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    padding: 6px 8px;
    font-size: 13px;
  }
  .ledger-detail { background: color-mix(in srgb, var(--card) 60%, var(--bg)); border-radius: 8px; padding: 10px 12px; margin: 6px 0 10px; }
  .session-block { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
  .session-block .session-header { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .chat-line { padding: 4px 0; font-size: 13px; white-space: pre-wrap; word-break: break-word; }
  .chat-line .who { font-weight: 600; margin-right: 6px; }
</style>
</head>
<body>

<div id="gate">
  <h1>WeChat Bot — Admin Panel</h1>
  <div class="subtitle">Enter the admin API key to continue. Stored only for this browser tab session.</div>
  <input type="password" id="keyInput" placeholder="Admin API key" autofocus>
  <button onclick="saveKeyAndLoad()">Continue</button>
  <div class="error" id="gateError"></div>
</div>

<div id="app">
  <h1>WeChat Bot — Admin Panel</h1>
  <div class="subtitle">Thin client over the existing admin API. Read-only except Kefu staff role assignment below.</div>

  <div class="toolbar">
    <button onclick="loadAll()">↻ Refresh</button>
    <button class="secondary" onclick="clearKey()">Change key</button>
    <span class="empty" id="lastChecked"></span>
  </div>

  <div class="row">
    <div class="stat">
      <div class="label">Server</div>
      <div class="value"><span class="badge muted" id="healthBadge">…</span></div>
    </div>
    <div class="stat">
      <div class="label">Groups</div>
      <div class="value" id="groupCount">–</div>
    </div>
    <div class="stat">
      <div class="label">Total members</div>
      <div class="value" id="memberCount">–</div>
    </div>
    <div class="stat">
      <div class="label">Pending Kefu staff</div>
      <div class="value" id="kefuPendingCount">–</div>
    </div>
  </div>

  <div class="tabs">
    <button class="tab-btn active" data-tab="staff" onclick="switchTab('staff')">Staff &amp; Groups</button>
    <button class="tab-btn" data-tab="transactions" onclick="switchTab('transactions')">Transactions</button>
  </div>

  <div id="tab-staff" class="tab-panel active">
    <div class="card">
      <div class="card-header">
        <div>
          <h3>Kefu Staff</h3>
          <div class="meta">Assign a role to promote a staff member out of "pending". Warehouseman requires at least one warehouse.</div>
        </div>
        <button class="secondary" onclick="refreshKefuNames()">Refresh names from WeCom</button>
      </div>
      <table>
        <thead><tr><th>Name</th><th>external_userid</th><th>Role</th><th>Warehouse</th><th>Status</th><th>Registered</th><th></th></tr></thead>
        <tbody id="kefuStaffRows"></tbody>
      </table>
      <div class="error" id="kefuStaffError"></div>
    </div>

    <div id="groups"></div>
  </div>

  <div id="tab-transactions" class="tab-panel">
    <div class="card">
      <div class="card-header">
        <div>
          <h3>Transaction Ledger</h3>
          <div class="meta">Every request_log row, most recent first. Dates are UTC. Expand a row for its full conversation.</div>
        </div>
      </div>
      <div class="filters">
        <select id="ledgerStatus" onchange="reloadLedger()">
          <option value="">Any status</option>
          <option value="pending">pending</option>
          <option value="processing">processing</option>
          <option value="success">success</option>
          <option value="failed">failed</option>
          <option value="cancelled">cancelled</option>
          <option value="stale">stale</option>
        </select>
        <select id="ledgerChannel" onchange="reloadLedger()">
          <option value="">Any channel</option>
          <option value="smart_robot">Smart Bot</option>
          <option value="kefu">Kefu</option>
        </select>
        <input type="date" id="ledgerDateFrom" onchange="reloadLedger()">
        <span>to</span>
        <input type="date" id="ledgerDateTo" onchange="reloadLedger()">
        <button class="secondary" onclick="clearLedgerFilters()">Clear filters</button>
      </div>
      <table>
        <thead><tr>
          <th>Serial</th><th>Created At</th><th>Created By</th><th>Channel</th>
          <th>Service</th><th>Status</th><th>Completed At</th><th></th>
        </tr></thead>
        <tbody id="ledgerRows"></tbody>
      </table>
      <div class="error" id="ledgerError"></div>
      <div class="toolbar" style="margin-top:10px;margin-bottom:0;">
        <button class="secondary" id="ledgerLoadMore" onclick="loadMoreLedger()" style="display:none;">Load more</button>
      </div>
    </div>
  </div>

  <div class="error" id="loadError"></div>
</div>

<script>
const KEY_STORAGE = "wechat_bot_admin_key";

function getKey() { return sessionStorage.getItem(KEY_STORAGE); }
function clearKey() {
  sessionStorage.removeItem(KEY_STORAGE);
  document.getElementById("app").style.display = "none";
  document.getElementById("gate").style.display = "flex";
  document.getElementById("keyInput").value = "";
  document.getElementById("keyInput").focus();
}
function saveKeyAndLoad() {
  const val = document.getElementById("keyInput").value.trim();
  if (!val) return;
  sessionStorage.setItem(KEY_STORAGE, val);
  document.getElementById("gateError").textContent = "";
  loadAll();
}

async function authedFetch(path, options) {
  const resp = await fetch(path, {
    ...options,
    headers: { "X-Admin-Key": getKey(), ...(options && options.headers) },
  });
  if (resp.status === 401) {
    throw new Error("UNAUTHORIZED");
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try { detail = (await resp.json()).detail || detail; } catch {}
    throw new Error(`${path} -> ${detail}`);
  }
  return resp.json();
}

async function authedPatch(path, body) {
  return authedFetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function authedPost(path, body) {
  return authedFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function fmtDate(iso) {
  if (!iso) return "";
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

let _rolesCache = null;
let _warehouseCodesCache = null;
let _kefuStaffLabels = {};

async function getRoles() {
  if (_rolesCache) return _rolesCache;
  const resp = await authedFetch("/admin/roles");
  _rolesCache = resp.data || [];
  // Sourced from the same top-level key the server derives from
  // VALID_WAREHOUSE_CODES -- never hardcoded here, so this list can't
  // silently drift from what the backend actually accepts.
  _warehouseCodesCache = resp.warehouse_codes || [];
  return _rolesCache;
}

function warehouseChecksHtml(staffId, selectedCodes, visible) {
  const selected = new Set(selectedCodes || []);
  const boxes = (_warehouseCodesCache || []).map(code => `
    <label>
      <input type="checkbox" class="kefu-wh-box" data-staff="${staffId}" value="${escapeHtml(code)}"
             ${selected.has(code) ? "checked" : ""}>
      ${escapeHtml(code)}
    </label>
  `).join("");
  return `<div class="warehouse-checks" id="kefu-wh-${staffId}" style="${visible ? "" : "display:none"}">${boxes}</div>`;
}

function kefuRoleRowHtml(s, roles) {
  // Offer only roles accepted by the PATCH endpoint; internal roles such as
  // "pending" are not assignable.
  // The row's OWN current role is always included even when not
  // assignable, so a pending member's dropdown accurately shows "pending"
  // as selected rather than silently defaulting to whatever assignable
  // role happens to sort first.
  const assignableNames = roles.filter(r => r.assignable).map(r => r.name);
  const optionNames = assignableNames.includes(s.role) ? assignableNames : [s.role, ...assignableNames];
  const options = optionNames.map(r =>
    `<option value="${escapeHtml(r)}" ${r === s.role ? "selected" : ""}>${escapeHtml(r)}</option>`
  ).join("");
  const isWarehouseman = s.role === "warehouseman";
  return `
    <tr id="kefu-row-${s.staff_id}">
      <td>${escapeHtml(s.display_name || "(no name)")}</td>
      <td>${escapeHtml(s.external_userid)}</td>
      <td><select id="kefu-role-${s.staff_id}" onchange="onKefuRoleChange('${s.staff_id}')">${options}</select></td>
      <td>${warehouseChecksHtml(s.staff_id, s.warehouse_codes, isWarehouseman)}</td>
      <td>${s.is_active ? '<span class="badge ok">active</span>' : '<span class="badge bad">suspended</span>'}</td>
      <td>${fmtDate(s.created_at)}</td>
      <td class="actions">
        <button onclick="saveKefuRole('${s.staff_id}')">Save</button>
        <button class="secondary" onclick="deleteKefuStaff('${s.staff_id}')">Delete</button>
      </td>
    </tr>
  `;
}

function onKefuRoleChange(staffId) {
  const role = document.getElementById(`kefu-role-${staffId}`).value;
  document.getElementById(`kefu-wh-${staffId}`).style.display = role === "warehouseman" ? "" : "none";
}

async function saveKefuRole(staffId) {
  document.getElementById("kefuStaffError").textContent = "";
  document.getElementById("kefuStaffError").style.color = "";
  const role = document.getElementById(`kefu-role-${staffId}`).value;
  const body = { role };
  if (role === "warehouseman") {
    const codes = Array.from(document.querySelectorAll(`.kefu-wh-box[data-staff="${staffId}"]:checked`))
      .map(box => box.value);
    if (codes.length === 0) {
      document.getElementById("kefuStaffError").textContent = "At least one warehouse is required for role=warehouseman.";
      return;
    }
    body.warehouse_codes = codes;
  }
  try {
    await authedPatch(`/admin/kefu-staff/${staffId}`, body);
    await loadKefuStaff();
  } catch (e) {
    if (e.message === "UNAUTHORIZED") throw e;
    document.getElementById("kefuStaffError").textContent = "Save failed: " + e.message;
  }
}

async function refreshKefuNames() {
  document.getElementById("kefuStaffError").textContent = "";
  try {
    const resp = await authedPost("/admin/kefu-staff/refresh-names");
    const { checked, updated } = resp.data;
    document.getElementById("kefuStaffError").textContent = `Checked ${checked}, updated ${updated} name(s).`;
    document.getElementById("kefuStaffError").style.color = "var(--muted)";
    await loadKefuStaff();
  } catch (e) {
    if (e.message === "UNAUTHORIZED") throw e;
    document.getElementById("kefuStaffError").style.color = "var(--bad)";
    document.getElementById("kefuStaffError").textContent = "Refresh failed: " + e.message;
  }
}

async function loadKefuStaff() {
  const roles = await getRoles();
  const resp = await authedFetch("/admin/kefu-staff");
  const staff = resp.data || [];
  const pendingCount = staff.filter(s => s.role === "pending").length;
  document.getElementById("kefuPendingCount").textContent = pendingCount;

  _kefuStaffLabels = {};
  for (const s of staff) {
    _kefuStaffLabels[s.staff_id] = s.display_name || s.external_userid;
  }

  const rowsEl = document.getElementById("kefuStaffRows");
  rowsEl.innerHTML = staff.length
    ? staff.map(s => kefuRoleRowHtml(s, roles)).join("")
    : '<tr><td colspan="7" class="empty">No Kefu staff registered yet.</td></tr>';
}

async function deleteKefuStaff(staffId) {
  document.getElementById("kefuStaffError").textContent = "";
  document.getElementById("kefuStaffError").style.color = "";
  const label = _kefuStaffLabels[staffId] || staffId;
  if (!confirm(`Delete Kefu staff "${label}"? This cannot be undone.`)) return;
  try {
    await authedFetch(`/admin/kefu-staff/${staffId}`, { method: "DELETE" });
    await loadKefuStaff();
  } catch (e) {
    if (e.message === "UNAUTHORIZED") throw e;
    document.getElementById("kefuStaffError").style.color = "var(--bad)";
    document.getElementById("kefuStaffError").textContent = "Delete failed: " + e.message;
  }
}

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "transactions" && !_ledgerLoadedOnce) {
    _ledgerLoadedOnce = true;
    reloadLedger();
  }
}

// ── Transaction ledger ──────────────────────────────────────────────────────
let _ledgerLoadedOnce = false;
let _ledgerNextCursor = null;
let _ledgerExpanded = {};  // serial_number -> bool, so re-rendering a page doesn't collapse an open row

function ledgerFilterParams() {
  const params = {};
  const status = document.getElementById("ledgerStatus").value;
  const channel = document.getElementById("ledgerChannel").value;
  const dateFrom = document.getElementById("ledgerDateFrom").value;
  const dateTo = document.getElementById("ledgerDateTo").value;
  if (status) params.status = status;
  if (channel) params.source_channel = channel;
  // Plain <input type=date> gives "YYYY-MM-DD"; the server requires a full
  // ISO-8601 datetime and treats a naive one as UTC, so widen to the whole
  // day in UTC here rather than sending an ambiguous bare date.
  if (dateFrom) params.date_from = `${dateFrom}T00:00:00`;
  // .999999, not .000000/omitted -- the server's date_to bound is
  // inclusive at the exact instant given, so a bare T23:59:59 excludes
  // anything in that final fractional second (e.g. 23:59:59.5). Postgres
  // timestamptz's own precision ceiling is microseconds, so .999999 is
  // the true end of the selected day, not an approximation.
  if (dateTo) params.date_to = `${dateTo}T23:59:59.999999`;
  return params;
}

function clearLedgerFilters() {
  document.getElementById("ledgerStatus").value = "";
  document.getElementById("ledgerChannel").value = "";
  document.getElementById("ledgerDateFrom").value = "";
  document.getElementById("ledgerDateTo").value = "";
  reloadLedger();
}

async function reloadLedger() {
  document.getElementById("ledgerRows").innerHTML = "";
  _ledgerNextCursor = null;
  _ledgerExpanded = {};
  await loadMoreLedger();
}

async function loadMoreLedger() {
  document.getElementById("ledgerError").textContent = "";
  const params = new URLSearchParams(ledgerFilterParams());
  if (_ledgerNextCursor) params.set("cursor", _ledgerNextCursor);
  try {
    const resp = await authedFetch(`/admin/request-logs?${params.toString()}`);
    const rows = resp.data || [];
    _ledgerNextCursor = resp.next_cursor || null;

    const tbody = document.getElementById("ledgerRows");
    if (rows.length === 0 && tbody.children.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No matching requests.</td></tr>';
    } else {
      for (const r of rows) tbody.insertAdjacentHTML("beforeend", ledgerRowHtml(r));
    }
    document.getElementById("ledgerLoadMore").style.display = _ledgerNextCursor ? "" : "none";
  } catch (e) {
    if (e.message === "UNAUTHORIZED") throw e;
    document.getElementById("ledgerError").textContent = "Failed to load: " + e.message;
  }
}

function ledgerRowHtml(r) {
  const channelLabel = r.source_channel === "kefu" ? "Kefu" : "Smart Bot";
  return `
    <tr>
      <td>${escapeHtml(r.serial_number)}</td>
      <td>${fmtDate(r.created_at)}</td>
      <td>${escapeHtml(r.display_name || r.wechat_openid || "—")}</td>
      <td>${escapeHtml(channelLabel)}</td>
      <td>${escapeHtml(r.service_name || "—")}</td>
      <td>${escapeHtml(r.status)}</td>
      <td>${fmtDate(r.completed_at)}</td>
      <td><button class="secondary" onclick="toggleLedgerDetail('${r.serial_number}', this)">Details</button></td>
    </tr>
    <tr id="ledger-detail-row-${cssEscape(r.serial_number)}" style="display:none;">
      <td colspan="8"><div class="ledger-detail" id="ledger-detail-${cssEscape(r.serial_number)}"></div></td>
    </tr>
  `;
}

function cssEscape(s) {
  // serial numbers are REQ-YYYYMMDD-NNNNNN today, but escape defensively
  // rather than assume that shape forever.
  return String(s).replace(/[^a-zA-Z0-9_-]/g, "_");
}

async function toggleLedgerDetail(serial, buttonEl) {
  const rowId = `ledger-detail-row-${cssEscape(serial)}`;
  const containerId = `ledger-detail-${cssEscape(serial)}`;
  const row = document.getElementById(rowId);
  const nowOpen = row.style.display === "none";
  row.style.display = nowOpen ? "" : "none";
  buttonEl.textContent = nowOpen ? "Hide" : "Details";
  if (nowOpen && !_ledgerExpanded[serial]) {
    _ledgerExpanded[serial] = true;
    const container = document.getElementById(containerId);
    container.innerHTML = '<span class="empty">Loading…</span>';
    try {
      const resp = await authedFetch(`/admin/request-logs/${encodeURIComponent(serial)}`);
      container.innerHTML = ledgerDetailHtml(resp.data);
    } catch (e) {
      if (e.message === "UNAUTHORIZED") throw e;
      container.innerHTML = `<span class="error">Failed to load: ${escapeHtml(e.message)}</span>`;
      _ledgerExpanded[serial] = false;
    }
  }
}

function ledgerDetailHtml(detail) {
  const sessions = detail.sessions || [];
  if (sessions.length === 0) {
    return '<div class="empty">No conversation found for this request.</div>';
  }
  return sessions.map((s, i) => {
    const actorLabel = s.actor && s.actor.display_name
      ? s.actor.display_name
      : (s.actor && s.actor.id) || "unknown";
    const header = `Session ${i + 1} — ${escapeHtml(s.service_name || "?")} — ${escapeHtml(s.status)} — ${escapeHtml(actorLabel)} — ${fmtDate(s.created_at)}`;
    const lines = (s.conversation_history || []).map(turn => {
      const who = turn.role === "assistant" ? "Bot" : "User";
      // User-authored message text must never be interpolated unescaped
      // into innerHTML -- escapeHtml() here is mandatory, not optional.
      return `<div class="chat-line"><span class="who">${escapeHtml(who)}:</span>${escapeHtml(turn.content || "")}</div>`;
    }).join("");
    return `
      <div class="session-block">
        <div class="session-header">${header}</div>
        ${lines || '<div class="empty">(no turns recorded)</div>'}
      </div>
    `;
  }).join("");
}

async function loadAll() {
  document.getElementById("loadError").textContent = "";
  try {
    // health — unauthenticated
    try {
      const health = await (await fetch("/health")).json();
      const ok = health.data && health.data.status === "ok";
      const badge = document.getElementById("healthBadge");
      badge.textContent = ok ? "ok" : "down";
      badge.className = "badge " + (ok ? "ok" : "bad");
    } catch {
      const badge = document.getElementById("healthBadge");
      badge.textContent = "unreachable";
      badge.className = "badge bad";
    }

    await loadKefuStaff();

    const groupsResp = await authedFetch("/admin/groups");
    const groups = groupsResp.data || [];

    document.getElementById("gate").style.display = "none";
    document.getElementById("app").style.display = "block";
    document.getElementById("groupCount").textContent = groups.length;
    document.getElementById("lastChecked").textContent = "last checked " + new Date().toLocaleTimeString();

    let totalMembers = 0;
    const groupsEl = document.getElementById("groups");
    groupsEl.innerHTML = "";

    if (groups.length === 0) {
      groupsEl.innerHTML = '<div class="empty">No groups registered yet.</div>';
    }

    for (const g of groups) {
      let members = [];
      try {
        const membersResp = await authedFetch(`/admin/groups/${g.group_id}/members`);
        members = membersResp.data || [];
      } catch (e) {
        if (e.message === "UNAUTHORIZED") throw e;
        // leave members empty, show inline note instead of failing the whole page
      }
      totalMembers += members.length;

      const card = document.createElement("div");
      card.className = "card";

      const statusBadge = g.is_active
        ? '<span class="badge ok">active</span>'
        : '<span class="badge bad">inactive</span>';

      let rows = members.map(m => `
        <tr>
          <td>${escapeHtml(m.display_name || "(no name)")}</td>
          <td>${escapeHtml(m.wechat_openid)}</td>
          <td>${escapeHtml(m.role)}</td>
          <td>${escapeHtml((m.warehouse_codes || []).join(", ") || "—")}</td>
          <td>${m.is_active ? '<span class="badge ok">active</span>' : '<span class="badge bad">suspended</span>'}</td>
          <td>${fmtDate(m.joined_at)}</td>
        </tr>
      `).join("");

      if (members.length === 0) {
        rows = '<tr><td colspan="6" class="empty">No members yet.</td></tr>';
      }

      card.innerHTML = `
        <div class="card-header">
          <div>
            <h3>${escapeHtml(g.description || g.wechat_group_id)}</h3>
            <div class="meta">wechat_group_id: ${escapeHtml(g.wechat_group_id)} · ${statusBadge} · created ${fmtDate(g.created_at)}</div>
          </div>
        </div>
        <table>
          <thead><tr><th>Name</th><th>openid</th><th>Role</th><th>Warehouse</th><th>Status</th><th>Joined</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
      groupsEl.appendChild(card);
    }

    document.getElementById("memberCount").textContent = totalMembers;

  } catch (e) {
    if (e.message === "UNAUTHORIZED") {
      document.getElementById("gateError").textContent = "Invalid admin key.";
      clearKey();
    } else {
      document.getElementById("loadError").textContent = "Failed to load: " + e.message;
    }
  }
}

// keyboard convenience
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("keyInput").addEventListener("keydown", e => {
    if (e.key === "Enter") saveKeyAndLoad();
  });
  if (getKey()) {
    document.getElementById("gate").style.display = "none";
    document.getElementById("app").style.display = "block";
    loadAll();
  }
});
</script>

</body>
</html>
"""
