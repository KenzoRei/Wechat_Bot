"""
Read-only admin/dev panel — a single self-contained HTML page served
directly by the app (no separate hosting, no build step, no framework).

Deliberately NOT behind verify_admin_key at the route level — a browser has
to be able to load the page itself before it has a key to send. The page's
own JS prompts for the admin key once, stores it in sessionStorage, and
sends it as X-Admin-Key on every call to the already-protected /admin/*
endpoints (same auth as PowerShell/curl usage — this page has no elevated
access of its own, it's just a thin client over the existing admin API).
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
  <div class="subtitle">Read-only view over the existing admin API. No write actions happen from this page.</div>

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
  </div>

  <div id="groups"></div>
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

async function authedFetch(path) {
  const resp = await fetch(path, { headers: { "X-Admin-Key": getKey() } });
  if (resp.status === 401) {
    throw new Error("UNAUTHORIZED");
  }
  if (!resp.ok) {
    throw new Error(`${path} -> HTTP ${resp.status}`);
  }
  return resp.json();
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
          <td>${escapeHtml(m.warehouse_code || "—")}</td>
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
