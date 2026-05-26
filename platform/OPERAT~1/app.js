// AAuth Mission Platform — operator console.
// Vanilla JS, no build step.

// Backend endpoints. The console is served from :9002 by default; the platform
// services live on :9000 (registry) and :9001 (mission). When the user serves
// the console behind a reverse proxy, they can override these via window.AAUTH_CFG.
const CFG = Object.assign({
  registryUrl: localStorage.getItem("aauth.registryUrl") || "http://localhost:9000",
  missionUrl:  localStorage.getItem("aauth.missionUrl")  || "http://localhost:9001",
}, window.AAUTH_CFG || {});

// Session — basic auth header stored in sessionStorage so a tab refresh keeps
// the user logged in but closing the browser logs them out.
let AUTH = sessionStorage.getItem("aauth.basic") || null;
let USER = sessionStorage.getItem("aauth.user") || null;
let LAST_BOOTSTRAP = null;
let CURRENT_MISSION = null;

// ---- HTTP helpers ----------------------------------------------------------
async function api(svc, path, opts = {}) {
  const base = (svc === "registry") ? CFG.registryUrl : CFG.missionUrl;
  const headers = Object.assign(
    { "Content-Type": "application/json", "Accept": "application/json" },
    opts.headers || {},
    AUTH ? { "Authorization": "Basic " + AUTH } : {},
  );
  const r = await fetch(base + path, { ...opts, headers });
  if (r.status === 401) { signOut(); throw new Error("unauthorized"); }
  if (!r.ok) {
    let body = ""; try { body = await r.text(); } catch (_) {}
    throw new Error(`${r.status} ${r.statusText} — ${body.slice(0, 200)}`);
  }
  // policy/render returns text/yaml; everything else is JSON.
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("yaml") || ct.includes("text/plain")) return r.text();
  if (r.status === 204) return null;
  return r.json();
}

// ---- auth ------------------------------------------------------------------
async function doLogin(ev) {
  ev.preventDefault();
  const u = document.getElementById("u").value;
  const p = document.getElementById("p").value;
  AUTH = btoa(u + ":" + p);
  // Probe an authenticated endpoint to verify creds.
  try {
    await api("registry", "/v1/stats");
    sessionStorage.setItem("aauth.basic", AUTH);
    sessionStorage.setItem("aauth.user",  u);
    USER = u;
    enterApp();
  } catch (e) {
    AUTH = null;
    const err = document.getElementById("login-err");
    err.style.display = "block";
    err.textContent = "Invalid credentials, or platform services unreachable. Check that registry-service :9000 and mission-service :9001 are running.";
  }
  return false;
}

function signOut() {
  AUTH = USER = null;
  sessionStorage.clear();
  document.getElementById("app").style.display = "none";
  document.getElementById("login").style.display = "flex";
}

function enterApp() {
  document.getElementById("login").style.display = "none";
  document.getElementById("app").style.display = "grid";
  document.getElementById("whoami").textContent = USER;
  loadDashboard();
}

// ---- routing ---------------------------------------------------------------
document.addEventListener("click", (ev) => {
  const t = ev.target.closest(".tab");
  if (!t) return;
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  const id = "page-" + t.dataset.page;
  document.getElementById(id).classList.add("active");
  const loader = { dashboard: loadDashboard, agents: loadAgents, missions: loadMissions,
                   tokens: loadTokens, gateway: loadGateway, audit: loadAudit };
  loader[t.dataset.page]?.();
});

function closeDialog(id) { document.getElementById(id).style.display = "none"; }

// ---- formatters ------------------------------------------------------------
const fmtTime = (sec) => sec ? new Date(sec * 1000).toLocaleString() : "—";
const fmtShort = (s, n = 12) => s ? (s.length > n ? s.slice(0, n) + "…" : s) : "—";
const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const agentSlug = (url) => (url || "").split("/").pop() || "—";

function badge(state) {
  return `<span class="badge ${escapeHtml(state)}">${escapeHtml(state)}</span>`;
}

// ---- dashboard -------------------------------------------------------------
async function loadDashboard() {
  try {
    const [agentStats, missionStats, audit] = await Promise.all([
      api("registry", "/v1/stats"),
      api("mission",  "/v1/stats"),
      api("mission",  "/v1/audit?limit=20"),
    ]);
    document.getElementById("s-agents-active").textContent  = agentStats.agents_active;
    document.getElementById("s-agents-pending").textContent = agentStats.agents_pending;
    document.getElementById("s-missions-active").textContent= missionStats.missions_active;
    document.getElementById("s-tokens-total").textContent   = missionStats.tokens_total;
    renderAudit(audit, "dash-feed");
  } catch (e) { toast("dashboard load failed: " + e.message); }
}

// ---- agents ----------------------------------------------------------------
async function loadAgents() {
  const tbody = document.getElementById("agents-tbody");
  tbody.innerHTML = `<tr><td colspan="7" class="empty">Loading…</td></tr>`;
  try {
    const agents = await api("registry", "/v1/agents");
    if (!agents.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty">No agents registered yet. Click “Register agent” to start.</td></tr>`;
      return;
    }
    tbody.innerHTML = agents.map(a => `
      <tr>
        <td>${escapeHtml(a.display_name)}<div style="color:var(--text-mute);font-size:11.5px">${escapeHtml(a.owner_contact || "")}</div></td>
        <td><span class="id-chip">${escapeHtml(a.agent_id_url)}</span></td>
        <td>${badge(a.lifecycle_state)}</td>
        <td>${escapeHtml(a.owning_team)}</td>
        <td>${(a.allowed_downstream_agents || []).map(d => `<span class="id-chip" style="margin-right:4px">${escapeHtml(d)}</span>`).join("") || "—"}</td>
        <td class="mono">${escapeHtml((a.jwks_thumbprint || "—").slice(0, 24))}</td>
        <td>
          ${a.lifecycle_state === "active" ? `<button class="btn small" onclick="forceRotate('${a.id}')">Force rotate</button>` : ""}
          ${a.lifecycle_state !== "revoked" ? `<button class="btn small danger" onclick="revokeAgent('${a.id}')">Revoke</button>` : ""}
        </td>
      </tr>
    `).join("");
  } catch (e) { tbody.innerHTML = `<tr><td colspan="7" class="empty">load failed: ${escapeHtml(e.message)}</td></tr>`; }
}

function openRegisterDialog() {
  document.getElementById("reg-name").value = "";
  document.getElementById("reg-team").value = "";
  document.getElementById("reg-contact").value = "";
  document.getElementById("reg-downstreams").value = "";
  document.getElementById("reg-depth").value = "3";
  document.getElementById("dlg-register").style.display = "flex";
}

async function submitRegister(ev) {
  ev.preventDefault();
  const body = {
    display_name: document.getElementById("reg-name").value.trim(),
    owning_team:  document.getElementById("reg-team").value.trim(),
    owner_contact: document.getElementById("reg-contact").value.trim() || null,
    allowed_downstream_agents: document.getElementById("reg-downstreams").value.split(",").map(s => s.trim()).filter(Boolean),
    max_delegation_depth: parseInt(document.getElementById("reg-depth").value, 10),
  };
  try {
    const r = await api("registry", "/v1/agents", { method: "POST", body: JSON.stringify(body) });
    closeDialog("dlg-register");
    LAST_BOOTSTRAP = r;
    document.getElementById("bs-id").textContent = r.agent_id_url;
    document.getElementById("bs-token").textContent = r.bootstrap_token;
    document.getElementById("bs-exp").textContent = fmtTime(r.bootstrap_token_expires_at);
    document.getElementById("dlg-bootstrap").style.display = "flex";
    loadAgents();
  } catch (e) { toast("register failed: " + e.message); }
  return false;
}

function copyBootstrap() {
  if (!LAST_BOOTSTRAP) return;
  navigator.clipboard.writeText(LAST_BOOTSTRAP.bootstrap_token)
    .then(() => toast("bootstrap token copied"));
}

async function forceRotate(agentId) {
  if (!confirm(`Send a force-rotate signal to ${agentId}?\n\nThe agent will pick this up on its next poll and run its own rotate flow. The previous JWKS stays valid for a 1h grace period.`)) return;
  try {
    await api("registry", `/v1/agents/${agentId}/force-rotate`, { method: "POST" });
    toast(`rotation requested for ${agentId}`);
    loadAgents();
  } catch (e) { toast("force-rotate failed: " + e.message); }
}

async function revokeAgent(agentId) {
  if (!confirm(`Revoke agent “${agentId}” permanently?\n\nThis invalidates its JWKS in the registry and propagates a deny policy to agentgateway within ~5s. Any in-flight A2A call from this agent will be rejected.`)) return;
  try {
    await api("registry", `/v1/agents/${agentId}`, { method: "DELETE" });
    toast(`agent ${agentId} revoked`);
    loadAgents();
  } catch (e) { toast("revoke failed: " + e.message); }
}

// ---- missions --------------------------------------------------------------
async function loadMissions() {
  const tbody = document.getElementById("missions-tbody");
  tbody.innerHTML = `<tr><td colspan="7" class="empty">Loading…</td></tr>`;
  const state = document.getElementById("mission-state-filter").value;
  const qs = state ? `?state=${encodeURIComponent(state)}` : "";
  try {
    const missions = await api("mission", "/v1/missions" + qs);
    if (!missions.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty">No missions in this state yet. Drive one via the demo: <code>make run</code> in the repo root.</td></tr>`;
      return;
    }
    tbody.innerHTML = missions.map(m => `
      <tr>
        <td><span class="id-chip">${escapeHtml(fmtShort(m.id, 18))}</span></td>
        <td>${escapeHtml(m.user_subject)}</td>
        <td>${escapeHtml(agentSlug(m.originator_agent_id))}</td>
        <td>${escapeHtml(m.scope)}</td>
        <td>${badge(m.state)}</td>
        <td class="mono">${fmtTime(m.created_at)}</td>
        <td><button class="btn small" onclick="viewMission('${m.id}')">View</button></td>
      </tr>
    `).join("");
  } catch (e) { tbody.innerHTML = `<tr><td colspan="7" class="empty">load failed: ${escapeHtml(e.message)}</td></tr>`; }
}

async function viewMission(id) {
  try {
    const m = await api("mission", `/v1/missions/${encodeURIComponent(id)}`);
    CURRENT_MISSION = m;
    const kv = document.getElementById("mission-detail-kv");
    kv.innerHTML = `
      <dt>Mission ID</dt><dd class="mono">${escapeHtml(m.id)}</dd>
      <dt>User</dt><dd>${escapeHtml(m.user_subject)}</dd>
      <dt>Originator</dt><dd><span class="id-chip">${escapeHtml(m.originator_agent_id)}</span></dd>
      <dt>Scope</dt><dd>${escapeHtml(m.scope)}</dd>
      <dt>State</dt><dd>${badge(m.state)}</dd>
      <dt>Created</dt><dd class="mono">${fmtTime(m.created_at)}</dd>
      <dt>Expires</dt><dd class="mono">${fmtTime(m.expires_at)}</dd>
      ${m.completed_at ? `<dt>Completed</dt><dd class="mono">${fmtTime(m.completed_at)}</dd>` : ""}
      ${m.revoked_at ? `<dt>Revoked</dt><dd class="mono">${fmtTime(m.revoked_at)} by ${escapeHtml(m.revoked_by || "—")}</dd>` : ""}
    `;
    document.getElementById("mission-hops").innerHTML = (m.hops || []).length
      ? m.hops.map((h, i) => `
        <div class="hop">
          <div class="from-to">hop ${i+1}: <span style="color:var(--accent)">${escapeHtml(agentSlug(h.from_agent_id))}</span> → <span style="color:var(--accent)">${escapeHtml(agentSlug(h.to_agent_id))}</span></div>
          <div class="act">act = [${(h.act_chain || []).map(a => escapeHtml(agentSlug(a))).join(" → ")}] · jti=${escapeHtml(h.token_jti || "—")} · ${fmtTime(h.at)}</div>
        </div>`).join("")
      : `<div class="empty">No hops logged yet.</div>`;
    document.getElementById("mission-tokens").innerHTML = (m.tokens || []).length
      ? `<table><thead><tr><th>jti</th><th>Audience</th><th>Expires</th><th>Status</th></tr></thead>
         <tbody>${m.tokens.map(t => `
           <tr><td class="mono">${escapeHtml(t.jti || "—")}</td>
               <td>${escapeHtml(agentSlug(t.audience))}</td>
               <td class="mono">${fmtTime(t.expires_at)}</td>
               <td>${t.revoked ? badge("revoked") : badge("active")}</td></tr>`).join("")}
         </tbody></table>`
      : `<div class="empty">No tokens logged yet.</div>`;
    const revokeBtn = document.getElementById("dlg-mission-revoke");
    revokeBtn.disabled = ["revoked","completed","failed"].includes(m.state);
    revokeBtn.onclick = () => revokeMission(m.id);
    document.getElementById("dlg-mission").style.display = "flex";
  } catch (e) { toast("mission load failed: " + e.message); }
}

// ---- issue mission --------------------------------------------------------
async function openIssueMissionDialog() {
  document.getElementById("iss-scope").value = "";
  document.getElementById("iss-user").value = "";
  document.getElementById("iss-ttl").value = "600";
  document.getElementById("iss-meta").value = "";
  document.getElementById("iss-seed-hop").checked = false;
  document.getElementById("iss-seed-target-field").style.display = "none";

  const origSel = document.getElementById("iss-originator");
  const targSel = document.getElementById("iss-seed-target");
  origSel.innerHTML = `<option value="">Loading…</option>`;
  targSel.innerHTML = "";
  try {
    const agents = (await api("registry", "/v1/agents")).filter(a => a.lifecycle_state === "active");
    if (!agents.length) {
      origSel.innerHTML = `<option value="" disabled>No active agents — register one first</option>`;
    } else {
      origSel.innerHTML = agents.map(a =>
        `<option value="${escapeHtml(a.agent_id_url)}" data-allowed='${escapeHtml(JSON.stringify(a.allowed_downstream_agents || []))}'>${escapeHtml(a.display_name)}  (${escapeHtml(a.agent_id_url)})</option>`
      ).join("");
      _refreshSeedTargets();
    }
  } catch (e) {
    origSel.innerHTML = `<option value="" disabled>load failed: ${escapeHtml(e.message)}</option>`;
  }
  document.getElementById("dlg-issue-mission").style.display = "flex";
}

function _refreshSeedTargets() {
  const orig = document.getElementById("iss-originator");
  const targ = document.getElementById("iss-seed-target");
  const opt = orig.selectedOptions[0];
  if (!opt) { targ.innerHTML = ""; return; }
  let allowed = [];
  try { allowed = JSON.parse(opt.dataset.allowed || "[]"); } catch (_) {}
  // The originator's allowed_downstream_agents list holds slugs; map to
  // full agent_id_urls using the platform base from the originator's URL.
  const platformBase = opt.value.replace(/\/agents\/[^/]+$/, "");
  if (!allowed.length) {
    targ.innerHTML = `<option value="" disabled>${escapeHtml(opt.text)} has no allowed downstreams</option>`;
    return;
  }
  targ.innerHTML = allowed.map(slug =>
    `<option value="${escapeHtml(platformBase + "/agents/" + slug)}">${escapeHtml(slug)}</option>`
  ).join("");
}

document.addEventListener("change", (ev) => {
  if (!ev.target) return;
  if (ev.target.id === "iss-originator") _refreshSeedTargets();
  if (ev.target.id === "iss-seed-hop") {
    document.getElementById("iss-seed-target-field").style.display = ev.target.checked ? "block" : "none";
  }
});

async function submitIssueMission(ev) {
  ev.preventDefault();
  const originator = document.getElementById("iss-originator").value;
  if (!originator) { toast("pick an originator agent"); return false; }
  let metadata = {};
  const metaText = document.getElementById("iss-meta").value.trim();
  if (metaText) {
    try { metadata = JSON.parse(metaText); }
    catch (e) { toast("metadata isn't valid JSON: " + e.message); return false; }
  }
  const body = {
    originator_agent_id: originator,
    scope: document.getElementById("iss-scope").value.trim(),
    user_subject: document.getElementById("iss-user").value.trim() || "operator-issued",
    ttl_seconds: parseInt(document.getElementById("iss-ttl").value, 10),
    metadata,
  };
  if (document.getElementById("iss-seed-hop").checked) {
    body.seed_hop_to = document.getElementById("iss-seed-target").value;
    if (!body.seed_hop_to) { toast("pick a seed-hop target"); return false; }
  }
  try {
    const r = await api("mission", "/v1/missions/issue", { method: "POST", body: JSON.stringify(body) });
    closeDialog("dlg-issue-mission");
    toast(`mission ${fmtShort(r.mission_id, 12)} issued — opening detail`);
    await loadMissions();
    viewMission(r.mission_id);
  } catch (e) { toast("issue failed: " + e.message); }
  return false;
}

async function revokeMission(id) {
  if (!confirm(`Revoke mission ${id}?\n\nAll tokens in its chain will be marked revoked, and agentgateway will deny any in-flight call carrying this mission_id within ~5s.`)) return;
  try {
    await api("mission", `/v1/missions/${encodeURIComponent(id)}/revoke`, { method: "POST" });
    toast(`mission revoked`);
    closeDialog("dlg-mission");
    loadMissions();
  } catch (e) { toast("revoke failed: " + e.message); }
}

// ---- tokens ----------------------------------------------------------------
async function loadTokens() {
  const tbody = document.getElementById("tokens-tbody");
  tbody.innerHTML = `<tr><td colspan="7" class="empty">Loading…</td></tr>`;
  const rv = document.getElementById("token-revoked-filter").value;
  const qs = rv ? `?revoked=${rv}` : "";
  try {
    const tokens = await api("mission", "/v1/tokens" + qs);
    if (!tokens.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty">No tokens yet. Tokens are logged when agents perform an RFC 8693 exchange at the IDP.</td></tr>`;
      return;
    }
    tbody.innerHTML = tokens.map(t => `
      <tr>
        <td class="mono">${escapeHtml(t.jti || "—")}</td>
        <td><span class="id-chip">${escapeHtml(agentSlug(t.subject))}</span></td>
        <td><span class="id-chip">${escapeHtml(agentSlug(t.audience))}</span></td>
        <td>${(t.act_chain || []).map(a => escapeHtml(agentSlug(a))).join(" → ") || "—"}</td>
        <td class="mono">${escapeHtml(fmtShort(t.mission_id, 14))}</td>
        <td class="mono">${fmtTime(t.expires_at)}</td>
        <td>${t.revoked ? badge("revoked") : badge("active")}</td>
      </tr>
    `).join("");
  } catch (e) { tbody.innerHTML = `<tr><td colspan="7" class="empty">load failed: ${escapeHtml(e.message)}</td></tr>`; }
}

// ---- gateway / IDP ---------------------------------------------------------
async function loadGateway() {
  try {
    const [idp, policy] = await Promise.all([
      api("registry", "/v1/idp-config"),
      api("registry", "/v1/policy/render"),
    ]);
    const kv = document.getElementById("idp-kv");
    kv.innerHTML = Object.entries(idp).map(([k, v]) =>
      `<dt>${escapeHtml(k)}</dt><dd class="mono">${escapeHtml(v || "—")}</dd>`).join("");
    document.getElementById("policy-pre").textContent = (typeof policy === "string") ? policy : JSON.stringify(policy, null, 2);
  } catch (e) { toast("gateway load failed: " + e.message); }
}

// ---- audit -----------------------------------------------------------------
async function loadAudit() {
  try {
    const events = await api("mission", "/v1/audit?limit=200");
    renderAudit(events, "audit-feed");
  } catch (e) { document.getElementById("audit-feed").textContent = "load failed: " + e.message; }
}

function renderAudit(events, targetId) {
  const tgt = document.getElementById(targetId);
  if (!events.length) { tgt.innerHTML = `<div class="empty">No events yet.</div>`; return; }
  tgt.innerHTML = events.map(e => {
    const time = new Date(e.at * 1000).toLocaleTimeString();
    let body = "";
    if (e.kind === "mission_created")  body = `mission <code>${escapeHtml(fmtShort(e.mission_id, 14))}</code> created by user=${escapeHtml(e.user)} originator=${escapeHtml(agentSlug(e.originator))}`;
    else if (e.kind === "mission_completed") body = `mission <code>${escapeHtml(fmtShort(e.mission_id, 14))}</code> completed`;
    else if (e.kind === "mission_failed")    body = `mission <code>${escapeHtml(fmtShort(e.mission_id, 14))}</code> failed`;
    else if (e.kind === "mission_revoked")   body = `mission <code>${escapeHtml(fmtShort(e.mission_id, 14))}</code> revoked`;
    else if (e.kind === "hop")               body = `${escapeHtml(agentSlug(e.from_agent_id))} → ${escapeHtml(agentSlug(e.to_agent_id))} · act=[${(e.act_chain||[]).map(a=>escapeHtml(agentSlug(a))).join(" → ")}]`;
    else if (e.kind === "token_issued")      body = `jti <code>${escapeHtml(fmtShort(e.jti, 16))}</code> for audience=${escapeHtml(agentSlug(e.audience))} ${e.revoked ? badge("revoked") : ""}`;
    else body = JSON.stringify(e);
    return `<div class="event"><div class="time">${time}</div><div class="kind">${escapeHtml(e.kind)}</div><div class="body">${body}</div></div>`;
  }).join("");
}

// ---- toast notifications ---------------------------------------------------
function toast(msg) {
  const t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText = "position:fixed;bottom:24px;right:24px;background:#0b1220;border:1px solid var(--border);padding:10px 14px;border-radius:6px;z-index:100;font-size:13px;max-width:420px";
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4500);
}

// ---- bootstrap -------------------------------------------------------------
if (AUTH) enterApp();
else document.getElementById("login").style.display = "flex";
