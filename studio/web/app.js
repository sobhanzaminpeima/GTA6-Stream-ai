/* GTA VI Growth Studio - Viral Clip Hunter dashboard (no build step). */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtT = (t) => (t ? new Date(t * 1000).toLocaleTimeString() : "—");
const fmtD = (s) => (s ? new Date(s).toLocaleString() : "—");
const num = (v, d = 0) => (v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));
const usd = (v) => (v === null || v === undefined ? "—" : "$" + Number(v).toFixed(v < 1 ? 3 : 2));
const pill = (v, extra = "") => `<span class="pill ${esc(v)} ${extra}">${esc(String(v ?? "—").replaceAll("_", " "))}</span>`;
const scoreCls = (s) => (s >= 90 ? "t90" : s >= 75 ? "t75" : s >= 60 ? "t60" : "low");
const STATE = { route: "overview", session: null, timer: null, cand: { sort: "score" }, project: null };
const TITLES = { overview: "Overview", radar: "Live Radar", streamers: "Streamers & Rights", chat: "Chat Intelligence", moments: "Viral Moments",
  studio: "AI Clip Studio", calendar: "Content Calendar", analytics: "YouTube Analytics", growth: "Growth Intelligence", costs: "Costs", settings: "Settings & Status" };

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(o.body); }
  const tok = localStorageGet("studio_token"); if (tok) o.headers["X-Studio-Token"] = tok;
  const r = await fetch(path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof data === "object" ? data.detail || JSON.stringify(data) : data);
  return data;
}
function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function toast(msg, ms = 4200) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast("⚠ " + e.message, 7000); throw e; } }
function modal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); $("#modal-body").innerHTML = ""; }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

// ---------------------------------------------------------------- router
function route() {
  const [r, arg] = (location.hash.slice(1) || "overview").split("/");
  STATE.route = TITLES[r] ? r : "overview";
  closeModal();
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on", a.getAttribute("href") === "#" + STATE.route));
  $("#title").textContent = TITLES[STATE.route];
  clearInterval(STATE.timer);
  VIEWS[STATE.route](arg).catch((e) => ($("#view").innerHTML = `<div class="empty"><b>Couldn't load this page</b>${esc(e.message)}</div>`));
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- live events (SSE)
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onopen = () => { $("#live-dot").className = "dot ok"; $("#live-text").textContent = "live"; };
  es.onerror = () => { $("#live-dot").className = "dot bad"; $("#live-text").textContent = "reconnecting…"; };
  es.addEventListener("candidate", (e) => { const d = JSON.parse(e.data).data; if (d.created) toast(`New viral candidate #${d.id} (score ${d.score})`); if (["moments", "overview"].includes(STATE.route)) softRefresh(); });
  es.addEventListener("notification", (e) => { loadBell(); });
  es.addEventListener("project", () => { if (STATE.route === "studio") softRefresh(); });
  es.addEventListener("job", (e) => { const d = JSON.parse(e.data).data; if (STATE.route === "studio" && STATE.project) softRefresh(); if (d.status === "failed") toast(`Job ${d.kind || d.id} failed: ${d.error || ""}`, 8000); });
  es.addEventListener("status", () => { if (["overview", "radar", "settings"].includes(STATE.route)) softRefresh(); });
}
let _soft = null;
function softRefresh() { clearTimeout(_soft); _soft = setTimeout(() => VIEWS[STATE.route](STATE.route === "studio" ? STATE.project : STATE.route === "chat" ? STATE.session : undefined, true), 600); }

async function loadBell() {
  const n = await api("/api/notifications");
  const unread = n.filter((x) => !x.read).length;
  $("#bell-n").textContent = unread || "";
  $("#notif").innerHTML = n.length ? n.map((x) => `<div><b>${esc(x.title)}</b> <span class="muted small">${fmtD(x.at)}</span><br><span class="small">${esc(x.body)}</span> ${x.link ? `<a href="${esc(x.link)}">open</a>` : ""}</div>`).join("") : '<div class="muted">No notifications.</div>';
}
$("#bell").onclick = async () => { $("#notif").classList.toggle("hidden"); await api("/api/notifications/read", { method: "POST" }).catch(() => {}); $("#bell-n").textContent = ""; };

// ---------------------------------------------------------------- views
const VIEWS = {};

VIEWS.overview = async () => {
  const d = await api("/api/overview");
  const c = d.costs;
  $("#view").innerHTML = `
  <div class="help">Hi! This studio watches GTA live chats, flags the moments viewers go wild over, and turns <b>authorized</b> footage into Shorts with your own commentary. Nothing is recorded without a rights record, and nothing is uploaded without your approval. New here? Press <b>Run simulation</b> to watch the whole flow on your own footage.</div>
  <div class="row" style="margin-bottom:14px"><button onclick="runSim(false)">▶ Run simulation (own footage)</button><button class="ghost" onclick="runSim(true)">Simulate a stream without permission</button><button class="ghost" onclick="location.hash='#radar'">Find live streams</button></div>
  <div class="grid g4">
    <div class="card kpi"><div class="l">Monitored streams</div><div class="v">${d.monitored.length}</div></div>
    <div class="card kpi"><div class="l">Chat messages / min</div><div class="v">${num(d.chat_messages_last_minute)}</div></div>
    <div class="card kpi"><div class="l">Viral candidates</div><div class="v">${num(d.candidates_total)}</div></div>
    <div class="card kpi"><div class="l">Spent this month</div><div class="v">${usd(c.month_usd)}</div><div class="small muted">budget ${usd(c.monthly_budget_usd)}</div></div>
  </div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h3>Live now</h3>${d.monitored.length ? `<table>${d.monitored.map((m) => `<tr><td>${esc(m.stream.title)} ${m.stream.is_simulation ? pill("SIMULATION", "sim") : ""}<div class="small muted">${esc(m.stream.channel_name)} · ${esc(m.stream.platform)}</div></td><td>${pill(m.status)}</td><td><a href="#chat/${m.session_id}">timeline →</a></td></tr>`).join("")}</table>` : '<div class="empty">No stream is being monitored.</div>'}</div>
    <div class="card"><h3>Highest-scoring moments <a class="small" href="#moments">all →</a></h3>${candTable(d.top_candidates, true)}</div>
    <div class="card"><h3>In production / ready for approval</h3>
      <div class="muted small">${d.in_production} Short(s) rendering</div>
      ${d.ready_for_approval.length ? d.ready_for_approval.map((p) => `<div style="padding:6px 0"><a href="#studio/${p.id}">#${p.id} ${esc(p.title || "untitled")}</a> ${pill("ready_for_approval")}</div>`).join("") : '<div class="empty">Nothing waiting for your approval.</div>'}</div>
    <div class="card"><h3>Latest published Shorts</h3>${d.latest_published.length ? `<table><tr><th>Title</th><th>Views</th><th>Subs</th></tr>${d.latest_published.map((v) => `<tr><td>${esc(v.title)}</td><td>${num(v.metrics?.views)}</td><td>${num(v.metrics?.subscribersGained)}</td></tr>`).join("")}</table><div class="small muted">— = not available from YouTube Analytics yet</div>` : '<div class="empty">No Shorts uploaded through the studio yet.</div>'}</div>
  </div>`;
};

async function runSim(restricted) {
  const r = await act(() => api("/api/simulate", { method: "POST", body: { record_media: true, restricted } }), restricted ? "Simulated third-party stream started: chat only, no recording." : "Simulation started: chat + authorized buffer. The moment arrives ~98 s in.");
  location.hash = `#chat/${r.session_id}`;
}

// ---- Live Radar
VIEWS.radar = async (_, soft) => {
  const f = STATE.radarF || (STATE.radarF = {});
  const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== "" && v !== undefined));
  const streams = await api("/api/streams?" + q);
  if (!soft || !$("#streams")) {
    $("#view").innerHTML = `
    <div class="help">Discovery uses the official YouTube Data API and Twitch Helix. A title that says "GTA 6" is <b>not</b> proof of GTA 6 gameplay: only the platform category or your own verification marks <i>confirmed gameplay</i>. Monitoring chat is allowed for public streams; recording needs a rights record (Streamers &amp; Rights).</div>
    <div class="row" style="margin-bottom:12px"><button onclick="runDiscovery()">Run discovery now</button><button class="ghost" onclick="runDiscovery(true)" title="YouTube streams the creator licensed as Creative Commons (CC BY): reuse, editing and monetization allowed with credit">Find free-to-reuse streams (CC BY)</button><button class="ghost" onclick="addStreamModal()">+ Add a stream manually</button><span id="disc" class="muted small"></span></div>
    <div class="filters">
      ${sel("live", "Live", [["", "Any"], ["true", "Live now"], ["false", "Offline"]])}
      ${sel("platform", "Platform", [["", "All platforms"], ["youtube", "YouTube"], ["twitch", "Twitch"], ["simulation", "Simulation"], ["upload", "Uploads"]])}
      <input placeholder="Language (en)" data-f="language" value="${esc(f.language || "")}" style="width:120px">
      <input placeholder="Min viewers" data-f="min_viewers" type="number" value="${esc(f.min_viewers || "")}" style="width:120px">
      <input placeholder="Max viewers" data-f="max_viewers" type="number" value="${esc(f.max_viewers || "")}" style="width:120px">
      ${sel("monitoring", "Monitoring", [["", "Any status"], ["MONITORING", "Monitoring"], ["BUFFERING", "Buffering"], ["IDLE", "Idle"], ["STOPPED", "Stopped"]])}
      ${sel("authorization", "Authorization", [["", "Any rights"], ["OWNED", "Owned"], ["EXPLICITLY_LICENSED", "Licensed"], ["AUTHORIZED_FOR_CLIPPING", "Authorized"], ["RESTRICTED", "Restricted"], ["UNKNOWN", "Unknown"]])}
      ${sel("license", "License", [["", "Any license"], ["creativeCommon", "Free to reuse (CC BY)"], ["standard", "Needs permission"]])}
      ${sel("content_type", "Content", [["", "Any content"], ["confirmed_gameplay", "Confirmed gameplay"], ["gta_v_gameplay", "GTA V gameplay"], ["gta_vi_commentary", "GTA VI commentary"], ["trailer_reaction", "Trailer reaction"], ["fan_concept", "Fan concept"], ["unverified", "Unverified"]])}
    </div><div id="streams" class="streams"></div>`;
    document.querySelectorAll("[data-f]").forEach((el) => el.addEventListener("change", () => { f[el.dataset.f] = el.value; VIEWS.radar(null, true); }));
  }
  $("#streams").innerHTML = streams.length ? streams.map(streamCard).join("") : `<div class="empty"><b>No streams yet</b>Run discovery (needs YOUTUBE_API_KEY and/or TWITCH_CLIENT_ID + SECRET in .env), add one manually, or start a simulation from Overview.</div>`;
  STATE.timer = setInterval(() => VIEWS.radar(null, true), 15000);
};
function sel(key, label, opts) { const v = (STATE.radarF || {})[key] || ""; return `<select data-f="${key}" title="${label}">${opts.map(([k, l]) => `<option value="${k}" ${k === v ? "selected" : ""}>${l}</option>`).join("")}</select>`; }
const CT = { confirmed_gameplay: "Confirmed gameplay", gta_v_gameplay: "GTA V gameplay", gta_vi_commentary: "GTA VI commentary", trailer_reaction: "Trailer reaction", fan_concept: "Fan concept", unverified: "Unverified" };
function streamCard(s) {
  const mon = ["MONITORING", "BUFFERING", "CONNECTED", "RECONNECTING", "PROCESSING", "SOURCE_RESTRICTED"].includes(s.monitoring_status);
  return `<div class="scard"><div class="thumb" style="${s.thumbnail_url ? `background-image:url('${esc(s.thumbnail_url)}')` : ""}">${s.is_live ? '<span class="live">LIVE</span>' : ""}<span class="viewers">${num(s.viewer_count)} viewers</span></div>
  <div class="body"><div class="t">${esc(s.title)} ${s.is_simulation ? pill("SIMULATION", "sim") : ""}</div>
  <div class="small muted">${esc(s.channel_name)} · ${esc(s.platform)} · ${esc(s.language || "?")}</div>
  <div class="row">${s.license === "creativeCommon" ? '<span class="pill ok" title="Creative Commons Attribution: reuse, edit and monetize with credit">CC BY · free to reuse</span>' : '<span class="pill" title="Standard YouTube/Twitch license: ask the creator first">needs permission</span>'} ${pill(s.authorization)} ${pill(s.monitoring_status)} <span class="pill" title="${esc(s.classification_reason)}">${esc(CT[s.content_type] || s.content_type)}</span></div>
  <div class="small muted">Chat: ${num(s.chat_last_minute)} msg/min · Candidates: ${s.candidate_count} · Chat ${s.chat_available === false ? "unavailable" : "available"}</div>
  <div class="row" style="margin-top:auto">
   ${mon ? `<button class="ghost small" onclick="stopMon(${s.session_id})">Stop</button><a class="small" href="#chat/${s.session_id}">timeline →</a>` :
   `<button class="small" onclick="startMon(${s.id}, false)">Monitor chat</button><button class="small ghost" ${s.can_record ? "" : "disabled title='Needs an OWNED / LICENSED / AUTHORIZED rights record with recording allowed'"} onclick="startMon(${s.id}, true)">Monitor + record</button>`}
   ${s.source_url && !s.source_url.startsWith("sim://") ? `<a class="small" target="_blank" rel="noopener" href="${esc(s.source_url)}">source ↗</a>` : ""}
   <button class="ghost small" onclick='streamEdit(${JSON.stringify({ id: s.id, ct: s.content_type, ms: s.media_source || "", can: s.can_record })})'>⋯</button>
  </div></div></div>`;
}
async function runDiscovery(cc) { $("#disc").textContent = "searching…"; const r = await act(() => api("/api/discovery/run", { method: "POST", body: { cc_only: !!cc } })); if (cc) { STATE.radarF.license = "creativeCommon"; } $("#disc").textContent = `YouTube: ${r.youtube.status}${r.youtube.detail ? " (" + r.youtube.detail + ")" : ""} · Twitch: ${r.twitch.status}${r.twitch.detail ? " (" + r.twitch.detail + ")" : ""} · found ${r.found}${r.youtube.cc_by != null ? ` · CC BY ${r.youtube.cc_by}` : ""}`; VIEWS.radar(cc ? undefined : null, !cc); }
async function startMon(id, rec) { const r = await act(() => api("/api/monitor/start", { method: "POST", body: { livestream_id: id, record_media: rec } }), rec ? "Monitoring started (recording only if rights allow)" : "Chat monitoring started"); if (r.status === "SOURCE_RESTRICTED") toast("Media restricted: " + r.status_detail, 8000); VIEWS.radar(null, true); }
async function stopMon(sid) { await act(() => api(`/api/monitor/${sid}/stop`, { method: "POST" }), "Stopped"); softRefresh(); }
function addStreamModal() {
  modal(`<h2>Add a stream manually</h2><div class="grid g2"><div><label>Platform</label><select id="as-p"><option>youtube</option><option>twitch</option></select></div><div><label>Stream / video id (YouTube) or channel login (Twitch)</label><input id="as-id"></div>
  <div><label>Title</label><input id="as-t"></div><div><label>Channel name</label><input id="as-c"></div><div><label>Channel id (Twitch user id for chat)</label><input id="as-cid"></div><div><label>Source URL</label><input id="as-u" placeholder="https://..."></div></div>
  <div style="margin-top:14px"><button onclick="addStream()">Add</button></div>`);
}
async function addStream() { await act(() => api("/api/streams", { method: "POST", body: { platform: $("#as-p").value, stream_id: $("#as-id").value, title: $("#as-t").value, channel_name: $("#as-c").value, channel_id: $("#as-cid").value || null, source_url: $("#as-u").value } }), "Stream added"); closeModal(); VIEWS.radar(); }
function streamEdit(s) {
  modal(`<h2>Stream settings</h2><label>Content type (your verification wins over auto-classification)</label><select id="se-ct">${Object.entries(CT).map(([k, v]) => `<option value="${k}" ${k === s.ct ? "selected" : ""}>${v}</option>`).join("")}</select>
  <label>Ingest source for the rolling buffer (only for streams you own or are licensed to record: your OBS/RTMP/SRT/HLS output or a licensor-provided feed)</label><input id="se-ms" value="${esc(s.ms)}" ${s.can ? "" : "disabled placeholder='Add a rights record with recording allowed first'"}>
  <div style="margin-top:14px"><button onclick="saveStream(${s.id})">Save</button></div>`);
}
async function saveStream(id) { const body = { content_type: $("#se-ct").value }; if (!$("#se-ms").disabled) body.media_source = $("#se-ms").value; await act(() => api(`/api/streams/${id}`, { method: "PATCH", body }), "Saved"); closeModal(); VIEWS.radar(); }

// ---- Streamers & Rights
VIEWS.streamers = async () => {
  const [st, rights] = await Promise.all([api("/api/streamers"), api("/api/rights")]);
  $("#view").innerHTML = `
  <div class="help">Adding a streamer to the watchlist <b>never</b> starts recording. Public chat monitoring and media capture are separate permissions. Record permission in a rights record: what you may do, the evidence (email, contract, campaign terms) and when it expires. A permission is not a monetization guarantee.</div>
  <div class="grid g2">
   <div class="card"><h3>Add a streamer</h3><div class="grid g2"><div><label>Platform</label><select id="sa-p"><option>youtube</option><option>twitch</option></select></div><div><label>Channel URL, @handle or id</label><input id="sa-r" placeholder="https://www.twitch.tv/name"></div>
    <div><label>Display name (optional)</label><input id="sa-n"></div><div><label>Priority</label><select id="sa-pr"><option value="1">1 · high</option><option value="2">2</option><option value="3" selected>3</option><option value="4">4</option><option value="5">5 · low</option></select></div>
    <div><label>Language</label><input id="sa-l" value="en"></div><div><label>Content category</label><select id="sa-c">${Object.entries(CT).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div></div>
    <div style="margin-top:12px"><button onclick="addStreamer()">Add to watchlist</button></div></div>
   <div class="card"><h3>Record a permission</h3><div class="grid g2">
    <div><label>For streamer</label><select id="ri-s"><option value="">— (use source URL)</option>${st.map((x) => `<option value="${x.id}">${esc(x.name)} (${x.platform})</option>`).join("")}</select></div><div><label>…or one source URL</label><input id="ri-u"></div>
    <div><label>State</label><select id="ri-st"><option>OWNED</option><option>EXPLICITLY_LICENSED</option><option selected>AUTHORIZED_FOR_CLIPPING</option><option>RESTRICTED</option><option>UNKNOWN</option></select></div><div><label>Content owner</label><input id="ri-o"></div>
    <div style="grid-column:1/-1"><label>Evidence (link to email / contract / campaign terms)</label><input id="ri-e"></div>
    <div><label><input type="checkbox" id="ri-rec" style="width:auto"> Recording allowed</label><label><input type="checkbox" id="ri-ed" style="width:auto"> Editing allowed</label><label><input type="checkbox" id="ri-com" style="width:auto"> Commercial use (monetized)</label></div>
    <div><label>Attribution required</label><input id="ri-a" placeholder="Credit @name + link"><label>Expires</label><input id="ri-x" type="date"></div></div>
    <div style="margin-top:12px"><button onclick="addRights()">Save permission</button></div></div>
  </div>
  <div class="card" style="margin-top:16px"><h3>Watchlist</h3>${st.length ? `<table><tr><th>Streamer</th><th>Platform</th><th>Priority</th><th>Category</th><th>Rights</th><th>Candidates</th><th>Auto-monitor</th><th>Keywords</th><th></th></tr>${st.map((x) => `<tr><td><b>${esc(x.name)}</b><div class="small muted">${esc(x.channel_url || x.channel_id)}</div></td><td>${esc(x.platform)}</td><td>${x.priority}</td><td>${esc(CT[x.content_category] || x.content_category)}</td><td>${x.rights.length ? x.rights.map((r) => pill(r.state)).join(" ") : pill("UNKNOWN")}</td><td>${x.candidates}</td>
   <td><input type="checkbox" style="width:auto" ${x.active_monitoring ? "checked" : ""} onchange="patchStreamer(${x.id}, {active_monitoring: this.checked})"></td>
   <td><input value="${esc((x.custom_keywords || []).join(", "))}" onchange="patchStreamer(${x.id}, {custom_keywords: this.value.split(',').map(s=>s.trim()).filter(Boolean)})" placeholder="e.g. leonida drift"></td>
   <td><button class="ghost small" onclick="delStreamer(${x.id})">Remove</button></td></tr>`).join("")}</table>` : '<div class="empty"><b>Watchlist is empty</b>Add the streamers you follow above.</div>'}</div>
  <div class="card" style="margin-top:16px"><h3>Permission records</h3>${rights.length ? `<table><tr><th>Scope</th><th>State</th><th>Owner</th><th>Evidence</th><th>Rec/Edit/Comm.</th><th>Expires</th><th></th></tr>${rights.map((r) => `<tr><td>${r.streamer_id ? "streamer #" + r.streamer_id : esc(r.source_url)}</td><td>${pill(r.state)}</td><td>${esc(r.content_owner)}</td><td class="small">${esc(r.evidence)}</td><td>${r.recording_allowed ? "✔" : "✖"} / ${r.editing_allowed ? "✔" : "✖"} / ${r.commercial_use ? "✔" : "✖"}</td><td>${fmtD(r.expires_at)}</td><td><button class="ghost small" onclick="delRights(${r.id})">Delete</button></td></tr>`).join("")}</table>` : '<div class="empty">No permissions recorded.</div>'}</div>`;
};
async function addStreamer() { await act(() => api("/api/streamers", { method: "POST", body: { platform: $("#sa-p").value, ref: $("#sa-r").value, name: $("#sa-n").value || null, priority: +$("#sa-pr").value, language: $("#sa-l").value, content_category: $("#sa-c").value } }), "Added to the watchlist (no recording started)"); VIEWS.streamers(); }
async function patchStreamer(id, body) { await act(() => api(`/api/streamers/${id}`, { method: "PATCH", body }), "Saved"); }
async function delStreamer(id) { if (confirm("Remove this streamer and their permission records?")) { await act(() => api(`/api/streamers/${id}`, { method: "DELETE" }), "Removed"); VIEWS.streamers(); } }
async function addRights() {
  const body = { streamer_id: $("#ri-s").value ? +$("#ri-s").value : null, source_url: $("#ri-u").value || null, state: $("#ri-st").value, content_owner: $("#ri-o").value || null, evidence: $("#ri-e").value || null,
    recording_allowed: $("#ri-rec").checked, editing_allowed: $("#ri-ed").checked, commercial_use: $("#ri-com").checked, attribution_required: $("#ri-a").value || null, expires_at: $("#ri-x").value ? $("#ri-x").value + "T23:59:59" : null };
  await act(() => api("/api/rights", { method: "POST", body }), "Permission saved"); VIEWS.streamers();
}
async function delRights(id) { if (confirm("Delete this permission record?")) { await act(() => api(`/api/rights/${id}`, { method: "DELETE" })); VIEWS.streamers(); } }

// ---- Chat Intelligence (timeline)
VIEWS.chat = async (sid, soft) => {
  const sessions = await api("/api/monitor/sessions");
  sid = sid ? +sid : STATE.session || sessions[0]?.id;
  STATE.session = sid;
  if (!sid) { $("#view").innerHTML = '<div class="empty"><b>No monitoring session yet</b>Start one from Live Radar, or run a simulation from Overview.</div>'; return; }
  if (!soft || !$("#tl")) {
    $("#view").innerHTML = `<div class="row" style="margin-bottom:12px"><select id="sess" style="width:auto">${sessions.map((s) => `<option value="${s.id}" ${s.id === sid ? "selected" : ""}>#${s.id} · ${esc(s.stream.title)} · ${s.status}</option>`).join("")}</select><span id="sstat"></span><span class="grow"></span><button class="ghost small" id="stopbtn">Stop monitoring</button></div>
    <div class="card"><h3>Stream timeline <span class="small muted">last 15 minutes · updates live</span></h3><div class="chart-wrap"><canvas id="tl" height="300"></canvas></div>
    <div class="legend"><span><i style="background:#FF2E97"></i>messages / s</span><span><i style="background:#FFE14D"></i>reaction weight</span><span><i style="background:#7A2BFF"></i>anomaly score</span><span><i style="background:#3DDC97"></i>audio excitement</span><span><i style="background:#FF8C28"></i>visual activity</span><span><i style="background:#fff"></i>candidate</span><span><i style="background:#666"></i>suppressed spike</span></div></div>
    <div class="grid g2" style="margin-top:16px"><div class="card"><h3>Detections (with reasons)</h3><div id="spk" class="chatlog"></div></div><div class="card"><h3>Live chat (normalized, retention ${7} days)</h3><div id="clog" class="chatlog"></div></div></div>`;
    $("#sess").onchange = (e) => (location.hash = "#chat/" + e.target.value);
    $("#stopbtn").onclick = () => stopMon(sid);
  }
  const t = await api(`/api/monitor/${sid}/timeline`);
  $("#sstat").innerHTML = `${pill(t.session.status)} <span class="small muted">${esc(t.session.status_detail || "")} · ${num(t.session.messages_seen)} messages${t.session.buffer_enabled ? " · buffer " + t.session.buffer_minutes + " min" : " · no media buffer"}</span>`;
  drawTimeline($("#tl"), t);
  $("#spk").innerHTML = t.spikes.length ? t.spikes.slice().reverse().map((s) => `<div>${fmtT(s.t)} ${s.suppressed ? pill("suppressed") : pill("spike", "ok")} <b>${num(s.score, 1)}</b> ${s.candidate_id ? `<a href="#moments">candidate #${s.candidate_id}</a>` : ""}<div class="small muted">${esc(s.explanation)}</div></div>`).join("") : '<div class="muted">No spikes yet. The baseline needs ~60 s of chat first.</div>';
  $("#clog").innerHTML = t.recent_chat.map((m) => `<div class="${m.spam ? "spam" : ""}"><span class="muted small">${fmtT(m.t)}</span> ${esc(m.text)} ${m.kw?.length ? `<span class="kw small">[${esc(m.kw.join(", "))}]</span>` : ""} ${m.kind !== "message" ? pill(m.kind) : ""}</div>`).join("");
  STATE.timer = STATE.timer || null;
  clearInterval(STATE.timer);
  STATE.timer = setInterval(() => STATE.route === "chat" && VIEWS.chat(sid, true), 2000);
};
function drawTimeline(cv, t) {
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = 300;
  cv.width = W * dpr; cv.height = H * dpr; const g = cv.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
  const pts = t.points; if (!pts.length) { g.fillStyle = "#9A90B5"; g.font = "14px Bahnschrift"; g.fillText("Waiting for chat…", 16, 30); return; }
  const t0 = pts[0].t, t1 = Math.max(pts[pts.length - 1].t, t0 + 30), x = (tt) => 40 + ((tt - t0) / (t1 - t0)) * (W - 50);
  const top = 12, bot = H - 26, y = (v, m) => bot - (v / (m || 1)) * (bot - top);
  g.strokeStyle = "#2A2140"; g.lineWidth = 1; for (let i = 0; i <= 4; i++) { const yy = top + (i * (bot - top)) / 4; g.beginPath(); g.moveTo(40, yy); g.lineTo(W - 10, yy); g.stroke(); }
  g.fillStyle = "#9A90B5"; g.font = "11px Bahnschrift"; for (let i = 0; i <= 5; i++) { const tt = t0 + ((t1 - t0) * i) / 5; g.fillText(new Date(tt * 1000).toLocaleTimeString(), x(tt) - 24, H - 8); }
  for (const s of t.segments) { if (s.audio != null) { g.fillStyle = `rgba(61,220,151,${0.08 + s.audio / 260})`; g.fillRect(x(s.t), bot - 14, Math.max(1, x(s.t + s.d) - x(s.t)), 7); } if (s.visual != null) { g.fillStyle = `rgba(255,140,40,${0.08 + s.visual / 260})`; g.fillRect(x(s.t), bot - 6, Math.max(1, x(s.t + s.d) - x(s.t)), 6); } }
  const mM = Math.max(5, ...pts.map((p) => p.messages)), mR = Math.max(5, ...pts.map((p) => p.reaction)), mS = Math.max(6, ...pts.map((p) => p.score));
  for (const p of pts) { g.fillStyle = "rgba(255,225,77,.55)"; const h = (p.reaction / mR) * (bot - top) * 0.5; g.fillRect(x(p.t) - 1, bot - 16 - h, Math.max(1.5, (W - 50) / (t1 - t0) - 1), h); }
  const line = (key, max, col, w) => { g.strokeStyle = col; g.lineWidth = w; g.beginPath(); pts.forEach((p, i) => (i ? g.lineTo(x(p.t), y(p[key], max) - 16) : g.moveTo(x(p.t), y(p[key], max) - 16))); g.stroke(); };
  line("messages", mM, "#FF2E97", 2); line("score", mS, "#7A2BFF", 1.5);
  g.fillStyle = "#FF2E97"; g.fillText(`${mM} msg/s`, 4, top + 8);
  for (const s of t.spikes) { g.fillStyle = s.suppressed ? "#666" : "#FF2E97"; g.beginPath(); g.arc(x(s.t), top + 6, 3.5, 0, 7); g.fill(); }
  for (const c of t.candidates) { if (c.t < t0) continue; g.strokeStyle = "#fff"; g.setLineDash([4, 4]); g.beginPath(); g.moveTo(x(c.t), top); g.lineTo(x(c.t), bot); g.stroke(); g.setLineDash([]); g.fillStyle = "#fff"; g.fillText(`#${c.id} · ${c.score}`, x(c.t) + 4, top + 22); }
}

// ---- Viral Moments
function candTable(list, compact) {
  if (!list.length) return '<div class="empty"><b>No moments yet</b>Candidates appear here the second chat spikes.</div>';
  return `<table><tr>${compact ? "" : '<th data-s="time">Time</th>'}<th>Stream</th><th>Moment</th><th data-s="score">Score</th>${compact ? "" : "<th>Confidence</th><th>Rights</th><th>Clip</th><th>Production</th><th></th>"}</tr>
  ${list.map((c) => `<tr>${compact ? "" : `<td class="small">${fmtT(c.event_ts)}<div class="muted">media ${c.media_ts != null ? c.media_ts.toFixed(1) + "s ±" + (c.media_ts_uncertainty ?? 0).toFixed(1) : "—"}</div></td>`}
  <td>${esc(c.stream.channel_name)} ${c.stream.is_simulation ? pill("SIM", "sim") : ""}<div class="small muted">${esc(c.stream.platform)}</div></td>
  <td class="small">${esc(c.description)}${c.notes ? `<div class="muted">📝 ${esc(c.notes)}</div>` : ""}</td>
  <td><span class="score ${scoreCls(c.overall_score)}">${num(c.overall_score)}</span><div class="small muted">${esc(c.tier)}</div></td>
  ${compact ? "" : `<td class="small">${esc(c.confidence)}</td><td>${pill(c.rights_state)}</td><td>${pill(c.clip_readiness)}</td><td>${pill(c.production_status)}</td>
  <td><div class="row"><button class="small" onclick="candDetail(${c.id})">Open</button>${c.clip_readiness === "clip_ready" ? `<button class="small" onclick="genModal(${c.id})">Create Short</button>` : ""}</div></td>`}</tr>`).join("")}</table>`;
}
VIEWS.moments = async () => {
  const f = STATE.cand;
  const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== "" && v != null));
  const list = await api("/api/candidates?" + q);
  $("#view").innerHTML = `<div class="help">Scores are editorial heuristics (0–100), not view predictions. Chat-only scores are labelled <i>reduced confidence</i> until authorized media adds audio/visual evidence. Moments from streams without permission become a <b>timestamped report</b> with a permission-request draft, and no footage is taken.</div>
  <div class="filters"><select onchange="STATE.cand.status=this.value;VIEWS.moments()"><option value="">Active</option>${["new", "saved", "dismissed", "in_production", "ready_for_approval", "approved", "uploaded_private", "published"].map((s) => `<option ${f.status === s ? "selected" : ""}>${s}</option>`).join("")}</select>
  <input type="number" placeholder="Min score" style="width:120px" value="${esc(f.min_score || "")}" onchange="STATE.cand.min_score=this.value;VIEWS.moments()">
  <select onchange="STATE.cand.rights_state=this.value;VIEWS.moments()"><option value="">Any rights</option>${["OWNED", "EXPLICITLY_LICENSED", "AUTHORIZED_FOR_CLIPPING", "RESTRICTED", "UNKNOWN"].map((s) => `<option ${f.rights_state === s ? "selected" : ""}>${s}</option>`).join("")}</select>
  <select onchange="STATE.cand.platform=this.value;VIEWS.moments()"><option value="">Any platform</option>${["youtube", "twitch", "simulation", "upload"].map((s) => `<option ${f.platform === s ? "selected" : ""}>${s}</option>`).join("")}</select>
  <select onchange="STATE.cand.sort=this.value;VIEWS.moments()"><option value="score" ${f.sort === "score" ? "selected" : ""}>Sort: score</option><option value="time" ${f.sort === "time" ? "selected" : ""}>Sort: newest</option></select>
  <span class="grow"></span><button class="ghost" onclick="uploadModal()">⬆ Upload your own clip</button></div>
  <div class="card">${candTable(list)}</div>`;
};
async function candDetail(id) {
  const c = await api(`/api/candidates/${id}`);
  const e = c.explanation || {};
  const authorized = ["OWNED", "EXPLICITLY_LICENSED", "AUTHORIZED_FOR_CLIPPING"].includes(c.rights_state);
  modal(`<h2>Moment #${c.id} <span class="score ${scoreCls(c.overall_score)}">${num(c.overall_score)}</span> <span class="small muted">${esc(c.tier)} · ${esc(c.confidence)}</span></h2>
  <div class="row">${pill(c.rights_state)} ${pill(c.clip_readiness)} ${pill(c.production_status)} ${c.stream.is_simulation ? pill("SIMULATION", "sim") : ""}</div>
  <p>${esc(c.description)}</p>
  ${c.clip_url ? `<video controls src="${c.clip_url}#t=${Math.max(0, (c.media_ts - (e.media?.wide_in || 0)) - 5)}"></video><div class="small muted">Authorized extracted footage. The moment sits at ${c.media_ts?.toFixed(1)} s (±${(c.media_ts_uncertainty || 0).toFixed(1)} s).</div>` : `<div class="empty">No footage: ${authorized ? "waiting for post-roll / extraction" : "no media permission, so only the source link and timestamp are stored"}.</div>`}
  <div class="grid g2" style="margin-top:12px"><div><h3>Why it scored</h3><ul class="explain">${(e.score || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
   <h3>Timing</h3><div class="small muted">${e.timing ? `chat onset ${fmtT(e.timing.onset_wall)} − latency ${e.timing.chat_latency_s}s → event; media ${e.timing.media_ts ?? "—"}s ±${e.timing.uncertainty_s ?? "—"} (${esc(e.timing.method)})` : "—"}</div>
   ${c.media_ts != null ? `<label>Calibrate: true moment time in the clip's source (s)</label><div class="row"><input id="cal" type="number" step="0.1" style="width:140px" value="${c.media_ts.toFixed(1)}"><button class="ghost small" onclick="calibrate(${c.id})">Save calibration</button></div>` : ""}</div>
   <div><h3>Chat around the moment</h3><div class="chatlog" style="max-height:220px">${c.chat_sample.map((m) => `<div><span class="muted small">${fmtT(m.t)}</span> ${esc(m.text)}</div>`).join("") || '<span class="muted">—</span>'}</div></div></div>
  <label>Notes</label><textarea id="cn">${esc(c.notes || "")}</textarea>
  <div class="row" style="margin-top:12px"><button class="ghost" onclick="patchCand(${c.id},{notes:$('#cn').value})">Save notes</button><button class="ghost" onclick="patchCand(${c.id},{production_status:'saved'})">Save candidate</button><button class="warn" onclick="patchCand(${c.id},{production_status:'dismissed'})">Dismiss</button>
  <a href="#chat/${c.session_id}" onclick="closeModal()">Open timeline</a>${c.source_timestamp_url ? `<a target="_blank" rel="noopener" href="${esc(c.source_timestamp_url)}">Review source ↗</a>` : ""}<span class="grow"></span>
  <button class="ghost" onclick="showReport(${c.id})">Discovery report</button>${c.clip_readiness === "clip_ready" ? `<button onclick="genModal(${c.id})">GENERATE SHORT</button>` : ""}</div>`);
}
async function patchCand(id, body) { await act(() => api(`/api/candidates/${id}`, { method: "PATCH", body }), "Saved"); closeModal(); if (STATE.route === "moments") VIEWS.moments(); }
async function calibrate(id) { const r = await act(() => api(`/api/candidates/${id}/calibrate`, { method: "POST", body: { true_media_ts: +$("#cal").value } })); toast(`Calibrated. Implied chat latency ${r.chat_latency_s.toFixed(1)} s (used once 3+ calibrations exist).`); }
async function showReport(id) {
  const r = await api(`/api/candidates/${id}/report`);
  modal(`<h2>Discovery report #${id}</h2><table><tr><td>Stream</td><td>${esc(r.stream)} · ${esc(r.channel)}</td></tr><tr><td>When</td><td>${esc(r.event_time_utc)}</td></tr><tr><td>Source</td><td>${r.timestamp_url ? `<a target="_blank" rel="noopener" href="${esc(r.timestamp_url)}">${esc(r.timestamp_url)}</a>` : esc(r.source_url)}</td></tr><tr><td>Score</td><td>${r.score} · ${esc(r.tier)} · ${esc(r.confidence)}</td></tr><tr><td>Rights</td><td>${pill(r.rights.state)} ${esc(r.rights.reasons.join("; "))}</td></tr><tr><td>Media acquired</td><td>${r.media_acquired ? "yes (authorized)" : "no"}</td></tr></table>
  <label>Permission request draft (send it yourself, then record the answer under Streamers &amp; Rights)</label><textarea id="preq" style="min-height:140px">${esc(r.permission_request_draft)}</textarea><div class="row" style="margin-top:8px"><button class="ghost" onclick="navigator.clipboard.writeText($('#preq').value);toast('Copied')">Copy</button></div>`);
}
async function genModal(id) {
  const presets = await api("/api/presets");
  modal(`<h2>Generate Short from moment #${id}</h2><p class="muted">Runs the 14-stage workflow: rights → footage → analysis → cuts → original commentary → narration → captions → timeline → graphics → render → cover → metadata → QC → your review. Nothing is uploaded.</p>
  <div class="grid g2"><div><label>Editing preset</label><select id="g-p">${Object.keys(presets).map((p) => `<option ${p === "CINEMATIC" ? "selected" : ""}>${p}</option>`).join("")}</select></div>
  <div><label>Cut</label><select id="g-c"><option value="A">A · ~20 s</option><option value="B" selected>B · ~35 s</option><option value="C">C · ~50 s</option></select></div></div>
  <div style="margin-top:14px"><button onclick="generate(${id})">GENERATE SHORT</button></div>`);
}
async function generate(id) { const r = await act(() => api(`/api/candidates/${id}/generate`, { method: "POST", body: { preset: $("#g-p").value, cut: $("#g-c").value } }), "Production started"); closeModal(); location.hash = `#studio/${r.project_id}`; }
function uploadModal() {
  modal(`<h2>Upload your own recording or a licensed clip</h2><div class="grid g2"><div style="grid-column:1/-1"><label>Video file</label><input type="file" id="u-f" accept="video/*"></div>
  <div><label>Title</label><input id="u-t"></div><div><label>Owner</label><input id="u-o" value="GTA VI Central"></div>
  <div><label>Rights</label><select id="u-s"><option>OWNED</option><option>EXPLICITLY_LICENSED</option><option>AUTHORIZED_FOR_CLIPPING</option></select></div><div><label>Evidence (required if not owned)</label><input id="u-e"></div>
  <div><label>Moment at (seconds, empty = auto-detect)</label><input id="u-m" type="number" step="0.1"></div><div><label>Content type</label><select id="u-c">${Object.entries(CT).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div></div>
  <div style="margin-top:14px"><button onclick="doUpload()">Upload &amp; extract</button></div>`);
}
async function doUpload() {
  const fd = new FormData(); fd.append("file", $("#u-f").files[0]); fd.append("title", $("#u-t").value || "Upload"); fd.append("owner", $("#u-o").value); fd.append("state", $("#u-s").value); fd.append("evidence", $("#u-e").value); fd.append("content_type", $("#u-c").value);
  if ($("#u-m").value) fd.append("moment_at", $("#u-m").value);
  const r = await act(() => api("/api/uploads/source", { method: "POST", body: fd }), "Uploaded. Extracting the clip…"); closeModal(); toast(`Candidate #${r.candidate_id}: moment at ${r.moment_at.toFixed(1)} s (${r.how})`); VIEWS.moments();
}

// ---- AI Clip Studio / Shorts editor
const STAGES = ["verify_rights", "retrieve_footage", "analyze", "select_cuts", "commentary", "narration", "captions", "timeline", "graphics", "render", "cover", "metadata", "qc", "review"];
VIEWS.studio = async (pid, soft) => {
  if (!pid) {
    STATE.project = null;
    const ps = await api("/api/projects");
    $("#view").innerHTML = ps.length ? `<div class="card"><table><tr><th>#</th><th>Title</th><th>Preset</th><th>Status</th><th>Progress</th><th>Score</th><th></th></tr>${ps.map((p) => `<tr><td>${p.id}</td><td>${esc(p.title || "(untitled)")}</td><td>${esc(p.preset)}</td><td>${pill(p.status)}</td><td style="width:160px"><div class="progress"><div style="width:${p.progress * 100}%"></div></div><div class="small muted">${esc(p.stage || "")}</div></td><td>${num(p.candidate.score)}</td><td><a href="#studio/${p.id}">open →</a></td></tr>`).join("")}</table></div>` : '<div class="empty"><b>No Shorts in production</b>Open a moment with authorized footage and press GENERATE SHORT.</div>';
    STATE.timer = setInterval(() => STATE.route === "studio" && !STATE.project && VIEWS.studio(null, true), 4000);
    return;
  }
  STATE.project = +pid;
  const p = await api(`/api/projects/${pid}`);
  const st = p.stages || {}, sc = st.commentary?.output?.script, md = st.metadata?.output, qc = p.qc || {};
  const running = ["queued", "running"].includes(p.status) || ["queued", "running"].includes(p.job?.status);
  if (soft && $("#pv-status") && running) { $("#pv-status").innerHTML = statusBlock(p); return; }
  $("#view").innerHTML = `<div class="row" style="margin-bottom:10px"><a href="#studio">← all projects</a><span class="grow"></span>${pill(p.status)}</div>
  <div class="card" id="pv-status">${statusBlock(p)}</div>
  <div class="grid g2" style="margin-top:16px;grid-template-columns:minmax(0,380px) minmax(0,1fr)">
   <div class="card"><h3>Preview</h3>${p.files.short ? `<div class="phone"><video controls src="${p.files.short}"></video></div>` : '<div class="empty">Rendering…</div>'}
    <div class="row" style="margin-top:10px">${p.files.cover ? `<a target="_blank" href="${p.files.cover}"><img src="${p.files.cover}" style="width:110px;border-radius:8px"></a>` : ""}${p.files.thumbnail ? `<a target="_blank" href="${p.files.thumbnail}"><img src="${p.files.thumbnail}" style="width:170px;border-radius:8px"></a>` : ""}</div>
    ${p.files.contact_sheet ? `<a class="small" target="_blank" href="${p.files.contact_sheet}">contact sheet ↗</a>` : ""}</div>
   <div class="card"><h3>Commentary <span class="small muted">${esc(st.commentary?.output?.provider || "")} ${st.commentary?.output?.note ? "· " + esc(st.commentary.output.note).slice(0, 120) : ""}</span></h3>
    ${sc ? `<div class="small muted">${esc(sc.context_summary)}</div><label>Hooks (hookscore)</label>${(st.commentary.output.hookscore || []).map((h) => `<div class="small">${h.score != null ? `<b>${h.score}</b>` : "—"} · ${esc(h.hook)}</div>`).join("")}
    <label>Before the moment (one sentence per line)</label><textarea id="sc-i">${esc(sc.intro_lines.join("\n"))}</textarea><label>After the moment</label><textarea id="sc-o">${esc(sc.outro_lines.join("\n"))}</textarea>
    <div class="row" style="margin-top:8px"><button class="ghost small" onclick="saveScript(${p.id})">Save script &amp; re-voice changed lines</button></div>
    <details><summary class="small muted">Claims and evidence (${sc.claims.length})</summary>${sc.claims.map((c) => `<div class="small">• ${esc(c.claim)} <span class="muted">— ${esc(c.evidence)}</span></div>`).join("")}</details>` : '<div class="muted">Not written yet.</div>'}</div>
  </div>
  <div class="grid g3" style="margin-top:16px">
   <div class="card"><h3>Shorts editor</h3><label>Cut</label><select id="ed-cut">${(p.timeline?.cuts || []).map((c) => `<option value="${c.label}" ${c.label === p.timeline.selected_cut ? "selected" : ""}>${c.label} · ${c.duration}s (${c.in.toFixed(1)}–${c.out.toFixed(1)})</option>`).join("")}</select>
    <label>Manual trim (source seconds)</label><div class="row"><input id="ed-in" type="number" step="0.1" style="width:48%" placeholder="in"><input id="ed-out" type="number" step="0.1" style="width:48%" placeholder="out"></div>
    <label>Preset</label><select id="ed-pre">${["VIRAL GAMING", "CINEMATIC", "FUNNY MOMENTS", "GAMING NEWS", "EDUCATIONAL"].map((x) => `<option ${x === p.preset ? "selected" : ""}>${x}</option>`).join("")}</select>
    <div class="row" style="margin-top:10px"><button class="ghost small" onclick="saveTimeline(${p.id})">Apply &amp; re-render</button></div>
    <label>Regenerate one stage (earlier stages are kept)</label><div class="row"><select id="rg" style="width:auto">${STAGES.map((s) => `<option>${s}</option>`).join("")}</select><button class="ghost small" onclick="regen(${p.id})">Regenerate</button></div></div>
   <div class="card"><h3>Captions (SRT)</h3><textarea id="srt" style="min-height:230px" class="mono">${esc(p.srt)}</textarea><button class="ghost small" style="margin-top:8px" onclick="saveSrt(${p.id})">Save corrections &amp; re-burn</button></div>
   <div class="card"><h3>Quality checks</h3>${qc.checks ? `<div class="checks">${Object.entries(qc.checks).map(([k, v]) => `<div class="${v ? "y" : "n"}">${k.replaceAll("_", " ")}</div>`).join("")}</div><div class="small muted" style="margin-top:6px">Loudness ${qc.lufs ?? "—"} LUFS · reuse risk <b>${esc(qc.reuse_risk?.risk)}</b>: ${esc((qc.reuse_risk?.notes || []).join("; "))}</div>` : '<div class="muted">Runs after render.</div>'}</div>
  </div>
  ${md ? `<div class="grid g2" style="margin-top:16px"><div class="card"><h3>YouTube metadata</h3><label>Title options (title.py score)</label>${md.title_lint.map((t) => `<div class="small">${t.score != null ? `<b>${t.score}</b>` : "—"} · ${esc(t.title)} ${t.title === md.recommended_title ? pill("recommended", "ok") : ""}</div>`).join("")}
    <label>Description</label><textarea readonly style="min-height:150px">${esc(md.description)}</textarea><div class="small">Hashtags: ${esc(md.hashtags.join(" "))} · Tags: ${esc(md.tags.join(", "))}</div><div class="small">Playlist: ${esc(md.playlist_suggestion)} · Pinned comment: “${esc(md.pinned_comment)}”</div><div class="small muted">Next video idea: ${esc(md.related_video_idea)}</div></div>
    <div class="card"><h3>Approval &amp; publishing</h3>${approvalBlock(p)}</div></div>` : ""}`;
  if (running) STATE.timer = setInterval(() => STATE.route === "studio" && VIEWS.studio(pid, true), 2500);
};
function statusBlock(p) {
  const st = p.stages || {};
  return `<div class="row"><b>Project #${p.id}</b><span class="muted small">${esc(p.preset)} · ${esc(p.structure)}</span><span class="grow"></span>${p.job ? `${pill(p.job.status)} <span class="small muted">${esc(p.job.message || "")}</span>` : ""}${p.job && ["queued", "running"].includes(p.job.status) ? `<button class="ghost small" onclick="cancelJob(${p.job.id})">Cancel</button>` : ""}</div>
  <div class="progress" style="margin:10px 0"><div style="width:${(p.progress || 0) * 100}%"></div></div>
  <div class="stages">${STAGES.map((s) => `<span class="stage ${st[s] ? "done" : p.stage === s ? "cur" : ""}">${s.replaceAll("_", " ")}</span>`).join("")}</div>
  ${p.job?.error ? `<pre class="small mono" style="white-space:pre-wrap;color:#ff8da0">${esc(p.job.error.slice(0, 900))}</pre>` : ""}`;
}
function approvalBlock(p) {
  if (!p.export) return '<div class="muted">Available after QC.</div>';
  const ap = p.approval, up = p.upload;
  if (!ap || ap.decision !== "approved") {
    return `<div class="help">Watch the preview first. Approval allows a <b>private</b> upload unless you choose otherwise; going public always needs an explicit approval.</div>
    <div class="checks small"><label><input type="checkbox" class="ck" style="width:auto"> I watched the whole preview</label><label><input type="checkbox" class="ck" style="width:auto"> Title and cover match what the clip shows</label><label><input type="checkbox" class="ck" style="width:auto"> Footage rights and attribution are correct</label></div>
    <label>Approve for visibility</label><select id="ap-v"><option value="private">Private (default)</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select><label>Note</label><input id="ap-n">
    <div class="row" style="margin-top:10px"><button ${p.qc?.passed ? "" : "disabled title='QC has blocking failures'"} onclick="approveExp(${p.export.id},'approved')">Approve</button><button class="ghost" onclick="approveExp(${p.export.id},'changes_requested')">Request changes</button><button class="warn" onclick="approveExp(${p.export.id},'rejected')">Reject</button></div>
    ${ap ? `<div class="small muted">Last decision: ${esc(ap.decision)} by ${esc(ap.approver)} (${fmtD(ap.created_at)})</div>` : ""}`;
  }
  return `<div>${pill("approved", "ok")} for <b>${esc(ap.visibility_approved)}</b> by ${esc(ap.approver)} · ${fmtD(ap.created_at)}</div>
  ${up ? `<div class="small" style="margin-top:8px">Package: <span class="mono">${esc(up.package_dir)}</span><br>Upload: ${pill(up.status)} ${up.youtube_video_id ? `<a target="_blank" rel="noopener" href="https://youtube.com/shorts/${esc(up.youtube_video_id)}">${esc(up.youtube_video_id)} ↗</a>` : ""} ${up.error ? `<span class="muted">${esc(up.error.slice(0, 200))}</span>` : ""}</div>` : ""}
  <label>Privacy</label><select id="up-p"><option value="private">Private</option>${["unlisted", "public"].filter((v) => ["unlisted", "public"].indexOf(v) <= ["unlisted", "public"].indexOf(ap.visibility_approved)).map((v) => `<option>${v}</option>`).join("")}</select>
  <label>Schedule (optional, publishes from private at this time)</label><input id="up-at" type="datetime-local"><label>Playlist id (optional)</label><input id="up-pl" placeholder="PL...">
  <div class="row" style="margin-top:10px"><button class="ghost" onclick="prepUpload(${p.export.id})">Prepare upload package</button>${up && up.status !== "uploaded" ? `<button onclick="doYtUpload(${up.id})">Upload to YouTube</button>` : ""}</div>`;
}
async function approveExp(eid, decision) {
  if (decision === "approved" && [...document.querySelectorAll(".ck")].some((c) => !c.checked)) return toast("Tick the three checks first.");
  await act(() => api(`/api/exports/${eid}/approve`, { method: "POST", body: { decision, visibility: $("#ap-v").value, note: $("#ap-n").value, checklist: { watched: true, matches: true, rights: true } } }), "Decision saved"); VIEWS.studio(STATE.project);
}
async function prepUpload(eid) { const at = $("#up-at").value; await act(() => api(`/api/exports/${eid}/prepare-upload`, { method: "POST", body: { privacy: $("#up-p").value, publish_at: at ? new Date(at).toISOString() : null, playlist_id: $("#up-pl").value || null } }), "Upload package ready"); VIEWS.studio(STATE.project); }
async function doYtUpload(uid) { if (!confirm("Upload this approved Short to YouTube now?")) return; const r = await act(() => api(`/api/uploads/${uid}/upload`, { method: "POST" })); toast(r.duplicate ? `Already uploaded: ${r.youtube_video_id}` : `Uploaded: ${r.youtube_video_id}`, 8000); VIEWS.studio(STATE.project); }
async function saveScript(pid) { await act(() => api(`/api/projects/${pid}/script`, { method: "PUT", body: { intro_lines: $("#sc-i").value.split("\n").map((s) => s.trim()).filter(Boolean), outro_lines: $("#sc-o").value.split("\n").map((s) => s.trim()).filter(Boolean) } }), "Re-voicing changed lines…"); VIEWS.studio(pid); }
async function saveSrt(pid) { await act(() => api(`/api/projects/${pid}/captions`, { method: "PUT", body: { srt: $("#srt").value } }), "Re-burning captions…"); VIEWS.studio(pid); }
async function saveTimeline(pid) { const body = { selected_cut: $("#ed-cut").value, preset: $("#ed-pre").value }; if ($("#ed-in").value && $("#ed-out").value) { body.cut_in = +$("#ed-in").value; body.cut_out = +$("#ed-out").value; } await act(() => api(`/api/projects/${pid}/timeline`, { method: "PUT", body }), "Re-rendering…"); VIEWS.studio(pid); }
async function regen(pid) { await act(() => api(`/api/projects/${pid}/regenerate`, { method: "POST", body: { stage: $("#rg").value } }), "Regenerating"); VIEWS.studio(pid); }
async function cancelJob(id) { await act(() => api(`/api/jobs/${id}/cancel`, { method: "POST" }), "Cancel requested"); }

// ---- Calendar
VIEWS.calendar = async () => {
  const [items, ps] = await Promise.all([api("/api/calendar"), api("/api/projects")]);
  const days = {}; items.forEach((i) => (days[i.planned_for.slice(0, 10)] ||= []).push(i));
  const today = new Date(); const cells = [...Array(14)].map((_, k) => { const d = new Date(today); d.setDate(d.getDate() + k); return d.toISOString().slice(0, 10); });
  $("#view").innerHTML = `<div class="help">Rhythm over volume: about one Short a day and 1–2 long videos a week. Scheduling here is your plan; actual YouTube scheduling happens on upload (private until the time you set).</div>
  <div class="grid g4" style="grid-template-columns:repeat(7,minmax(0,1fr))">${cells.map((d) => `<div class="card" style="min-height:110px;padding:10px"><div class="small muted">${new Date(d).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</div>${(days[d] || []).map((i) => `<div class="small" style="margin-top:6px">• ${esc(i.title)} <a class="muted" href="javascript:delCal(${i.id})">✕</a></div>`).join("")}</div>`).join("")}</div>
  <div class="card" style="margin-top:16px"><h3>Plan a post</h3><div class="grid g3"><div><label>Title</label><input id="cl-t"></div><div><label>When</label><input id="cl-d" type="datetime-local"></div><div><label>Project</label><select id="cl-p"><option value="">—</option>${ps.map((p) => `<option value="${p.id}">#${p.id} ${esc(p.title || "")} (${p.status})</option>`).join("")}</select></div></div><button style="margin-top:10px" onclick="addCal()">Add</button></div>`;
};
async function addCal() { await act(() => api("/api/calendar", { method: "POST", body: { title: $("#cl-t").value || "Short", planned_for: new Date($("#cl-d").value).toISOString(), project_id: $("#cl-p").value ? +$("#cl-p").value : null } }), "Planned"); VIEWS.calendar(); }
async function delCal(id) { await api(`/api/calendar/${id}`, { method: "DELETE" }); VIEWS.calendar(); }

// ---- Analytics
VIEWS.analytics = async () => {
  const a = await api("/api/analytics");
  const m = (v, k) => (v.metrics && v.metrics[k] != null ? num(v.metrics[k], 1) : '<span class="muted" title="not returned by the API">n/a</span>');
  $("#view").innerHTML = `<div class="help">${esc(a.note)} ${a.connected ? "" : "<b>Connect your channel</b> under Settings to load real numbers."}</div>
  <div class="row" style="margin-bottom:12px"><button onclick="refreshAnalytics()" ${a.connected ? "" : "disabled"}>Refresh from YouTube Analytics</button></div>
  <div class="card">${a.videos.length ? `<table><tr><th>Short</th><th>Views</th><th>Watch min</th><th>Avg view (s)</th><th>Avg %</th><th>Subs</th><th>Likes</th><th>Comments</th><th>Top traffic source</th><th>Preset / hook / length</th></tr>${a.videos.map((v) => `<tr><td>${esc(v.title)}<div class="small muted">${fmtD(v.published_at)} · ${esc(v.youtube_video_id)}</div></td><td>${m(v, "views")}</td><td>${m(v, "estimatedMinutesWatched")}</td><td>${m(v, "averageViewDuration")}</td><td>${m(v, "averageViewPercentage")}</td><td>${m(v, "subscribersGained")}</td><td>${m(v, "likes")}</td><td>${m(v, "comments")}</td>
  <td class="small">${v.metrics?.traffic_sources ? esc(Object.entries(v.metrics.traffic_sources).sort((a, b) => b[1] - a[1])[0]?.join(": ")) : "n/a"}</td><td class="small">${esc(v.attributes?.editing_preset)} · ${esc(v.attributes?.hook_type || "—")} · ${num(v.attributes?.clip_duration, 1)}s</td></tr>`).join("")}</table>` : '<div class="empty"><b>No Shorts uploaded through the studio yet</b>Analytics appear after your first approved upload.</div>'}</div>`;
};
async function refreshAnalytics() { const r = await act(() => api("/api/analytics/refresh", { method: "POST" })); toast(`Analytics: ${r.status} ${r.rows ?? ""}`); VIEWS.analytics(); }

// ---- Growth
VIEWS.growth = async () => {
  const g = await api("/api/growth");
  const grp = (title, rows) => `<div class="card"><h3>${title}</h3>${rows?.length ? `<table><tr><th>Group</th><th>n</th><th>Median</th><th>Confidence</th></tr>${rows.map((r) => `<tr><td>${esc(r.group)}</td><td>${r.n}</td><td>${num(r.median, 1)}</td><td class="small">${esc(r.confidence)}</td></tr>`).join("")}</table>` : '<div class="muted small">No data.</div>'}</div>`;
  $("#view").innerHTML = `<div class="row" style="margin-bottom:12px"><button onclick="runGrowth()">Write this week's report</button><span class="muted small">Built only from real YouTube Analytics. Groups under 5 videos are hypotheses, never conclusions.</span></div>
  ${g ? `${g.data_note ? `<div class="help">${esc(g.data_note)}</div>` : ""}<div class="card"><h3>Recommendations · ${esc(g.week)}</h3><ul class="explain">${g.recommendations.map((r) => `<li>${esc(r)}</li>`).join("")}</ul><div class="small muted">Funnel: ${g.candidate_funnel.detected} detected · ${g.candidate_funnel.produced} produced · ${g.candidate_funnel.dismissed} dismissed</div></div>
  <div class="grid g2" style="margin-top:16px">${grp("Views by length", g.by_duration)}${grp("Views by hook type", g.by_hook_type)}${grp("Views by editing preset", g.by_preset)}${grp("Subscribers by topic", g.by_topic_subs)}${grp("Views by source", g.by_source)}
  <div class="card"><h3>Top Shorts by views</h3>${g.top_views.map((v) => `<div class="small">${num(v.metrics.views)} · ${esc(v.title)}</div>`).join("") || '<div class="muted small">—</div>'}<h3 style="margin-top:12px">Weakest retention</h3>${g.weak.map((v) => `<div class="small">${num(v.metrics.averageViewPercentage, 1)}% · ${esc(v.title)}</div>`).join("") || '<div class="muted small">—</div>'}<div class="small muted" style="margin-top:8px">${esc((g.unavailable_metrics || []).join("; "))}</div></div></div>` : '<div class="empty"><b>No report yet</b>Write one: it works even with little data and says so.</div>'}`;
};
async function runGrowth() { await act(() => api("/api/growth/run", { method: "POST" }), "Report written"); VIEWS.growth(); }

// ---- Costs
VIEWS.costs = async () => {
  const c = await api("/api/costs");
  const pct = (a, b) => Math.min(100, (a / (b || 1)) * 100);
  $("#view").innerHTML = `<div class="help">Chat analysis, spike detection, audio/visual signals, cutting and rendering run locally for free. Paid calls (Claude commentary, ElevenLabs voice) happen only when you enable them in Settings, and only inside your daily and monthly budget.</div>
  <div class="grid g4"><div class="card kpi"><div class="l">Today</div><div class="v">${usd(c.today_usd)}</div><div class="bar"><div style="width:${pct(c.today_usd, c.daily_budget_usd)}%"></div></div><div class="small muted">of ${usd(c.daily_budget_usd)}</div></div>
  <div class="card kpi"><div class="l">This month</div><div class="v">${usd(c.month_usd)}</div><div class="bar"><div style="width:${pct(c.month_usd, c.monthly_budget_usd)}%"></div></div><div class="small muted">of ${usd(c.monthly_budget_usd)}</div></div>
  <div class="card kpi"><div class="l">Avg per Short (actual)</div><div class="v">${usd(c.avg_actual_per_short)}</div><div class="small muted">${c.shorts_costed} Shorts · est. ${usd(c.avg_estimated_per_short)}</div></div>
  <div class="card kpi"><div class="l">YouTube quota today</div><div class="v">${num(c.youtube_quota_today)}</div><div class="bar"><div style="width:${pct(c.youtube_quota_today, c.youtube_quota_limit)}%"></div></div><div class="small muted">of ${num(c.youtube_quota_limit)} units</div></div></div>
  <div class="grid g2" style="margin-top:16px"><div class="card"><h3>Usage this month</h3><table><tr><th>Provider</th><th>Units</th><th>Calls</th><th>Cost</th></tr>${c.by_provider.map((r) => `<tr><td>${esc(r.provider)}</td><td>${num(r.units)} ${esc(r.unit_type)}</td><td>${r.calls}</td><td>${usd(r.usd)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">No usage yet.</td></tr>'}</table></div>
  <div class="card"><h3>Next Short (estimate if paid services are on)</h3><div>Claude commentary: ${usd(c.estimate_next_short.llm_usd)}</div><div>ElevenLabs narration: ${usd(c.estimate_next_short.tts_usd)}</div><div>Rendering: ${esc(c.estimate_next_short.render)}</div><div style="margin-top:6px"><b>Total ${usd(c.estimate_next_short.total_usd)}</b></div>
  <div class="small muted" style="margin-top:8px">Paid LLM: ${c.paid_llm_enabled ? "ON" : "off"} · Paid TTS: ${c.paid_tts_enabled ? "ON" : "off"}. Change it in Settings.</div></div></div>`;
};

// ---- Settings & status
VIEWS.settings = async () => {
  const [st, a, presets, auditL, jobsL, keys] = await Promise.all([api("/api/status"), api("/api/automation"), api("/api/presets"), api("/api/audit?limit=60"), api("/api/jobs"), api("/api/keys")]);
  const gb = (b) => (b / 1024 ** 3).toFixed(1) + " GB";
  const f = (k, label, type = "number", step = "any") => `<div><label>${label}</label><input data-a="${k}" type="${type}" step="${step}" value="${esc(a[k])}"></div>`;
  const b = (k, label) => `<div><label><input data-a="${k}" type="checkbox" style="width:auto" ${a[k] ? "checked" : ""}> ${label}</label></div>`;
  $("#view").innerHTML = `
  <div class="card"><h3>Connections <span class="small muted">real status, checked now</span></h3><table>${st.connections.map((c) => `<tr><td>${esc(c.platform)}</td><td>${pill(c.status)}</td><td class="small muted">${esc(c.detail || "")} ${c.account ? "· " + esc(c.account) : ""}</td></tr>`).join("")}</table>
   <div class="row" style="margin-top:10px"><a class="btn ${keys.oauth_client ? "" : "ghost"}" href="/oauth/youtube/start">Connect YouTube channel</a><span class="small muted">${keys.oauth_client ? "Opens Google sign-in; you choose the channel." : "Upload your OAuth client JSON below first."} Tokens are stored encrypted on this PC.</span></div></div>
  <div class="card" style="margin-top:16px"><h3>API keys <span class="small muted" title="${esc(keys.env_file)}">saved only on this PC (local .env file), never shown again</span></h3>
   <div class="grid g3">${[["YOUTUBE_API_KEY", "YouTube Data API key"], ["TWITCH_CLIENT_ID", "Twitch client ID"], ["TWITCH_CLIENT_SECRET", "Twitch client secret"], ["TWITCH_USER_TOKEN", "Twitch user token (user:read:chat)"], ["TWITCH_USER_ID", "Twitch user ID"], ["ELEVENLABS_API_KEY", "ElevenLabs API key"], ["ANTHROPIC_API_KEY", "Anthropic API key"]].map(([k, l]) => `<div><label>${l} ${keys.keys[k] ? pill("set", "ok") : pill("not set")}</label><input type="password" autocomplete="off" data-key="${k}" placeholder="${keys.keys[k] ? "•••••• (leave empty to keep)" : "paste key"}"></div>`).join("")}
   <div><label>Channel handle</label><input data-key="STUDIO_CHANNEL_HANDLE" value="${esc(keys.values.STUDIO_CHANNEL_HANDLE || "")}" placeholder="@yourchannel"></div><div><label>Channel name</label><input data-key="STUDIO_CHANNEL_NAME" value="${esc(keys.values.STUDIO_CHANNEL_NAME || "")}" placeholder="GTA VI Central"></div></div>
   <div class="row" style="margin-top:10px"><button onclick="saveKeys()">Save keys</button><label class="small" style="margin:0">OAuth client JSON (Desktop app) ${keys.oauth_client ? pill("saved", "ok") : pill("missing")}</label><input type="file" id="oauthf" accept=".json" style="width:auto"><button class="ghost small" onclick="uploadOauth()">Upload</button></div></div>
  <div class="card" style="margin-top:16px"><h3>Automation</h3><div class="grid g4">
   ${f("spike_sensitivity", "Spike sensitivity (z, lower = more sensitive)")}${f("min_messages_per_window", "Min messages in 10 s")}${f("merge_gap_s", "Merge spikes within (s)")}${f("max_candidates_per_hour", "Max candidates / hour")}
   ${f("min_candidate_score", "Min score to notify / auto-draft")}${f("buffer_minutes", "Buffer length (2–30 min)")}${f("pre_roll_s", "Seconds before the moment")}${f("post_roll_s", "Seconds after the moment")}
   ${f("chat_latency_s", "Chat latency offset (s)")}${f("daily_budget_usd", "Daily budget ($)")}${f("monthly_budget_usd", "Monthly budget ($)")}
   <div><label>Monitoring window</label><div class="row"><input data-w="start" type="time" value="${esc(a.monitoring_window.start)}" style="width:48%"><input data-w="end" type="time" value="${esc(a.monitoring_window.end)}" style="width:48%"></div></div>
   ${b("recording_enabled", "Authorized recording on")}${b("auto_generate_drafts", "Auto-generate drafts")}${b("allow_paid_llm", "Allow paid Claude calls")}${b("allow_paid_tts", "Allow paid ElevenLabs calls")}${b("notifications", "Notifications")}
   <div><label>Narrator</label><select data-n="provider"><option value="elevenlabs" ${a.narrator_preset.provider === "elevenlabs" ? "selected" : ""}>ElevenLabs · Brian</option><option value="windows_sapi" ${a.narrator_preset.provider === "windows_sapi" ? "selected" : ""}>Windows voice (free draft)</option><option value="silent_stub" ${a.narrator_preset.provider === "silent_stub" ? "selected" : ""}>Silent (testing)</option></select></div>
   <div><label>Narration speed</label><input data-n="speed" type="number" step="0.01" value="${esc(a.narrator_preset.speed)}"></div></div>
   <label>Score weights (chat / audio / visual / narrative / originality)</label><div class="row">${Object.entries(a.score_weights).map(([k, v]) => `<input data-sw="${k}" type="number" step="0.05" value="${v}" style="width:90px" title="${k}">`).join("")}</div>
   <label>Keyword weight overrides (JSON, e.g. {"CLIP": 1.5})</label><input id="kw" value='${esc(JSON.stringify(a.keyword_weights))}'>
   <div class="row" style="margin-top:12px"><button onclick="saveAutomation()">Save settings</button><span class="small muted">Publishing: default private, approval always required.</span></div></div>
  <div class="card" style="margin-top:16px"><h3>Editing presets</h3><table><tr><th>Preset</th><th>Layout</th><th>Zoom</th><th>Music dB</th><th>Source dB</th><th>Fade s</th><th>Caption size</th><th></th></tr>${Object.entries(presets).map(([n, p]) => `<tr data-p="${esc(n)}"><td>${esc(n)}</td><td><select data-k="layout"><option ${p.layout === "fit" ? "selected" : ""}>fit</option><option ${p.layout === "focus" ? "selected" : ""}>focus</option></select></td>${["zoom", "music_db", "source_db", "fade", "caption_size"].map((k) => `<td><input data-k="${k}" type="number" step="any" value="${p[k]}" style="width:80px"></td>`).join("")}<td><button class="ghost small" onclick="savePreset(this)">Save</button></td></tr>`).join("")}</table></div>
  <div class="grid g2" style="margin-top:16px"><div class="card"><h3>System status</h3><div class="small">Workers: ${st.workers ? pill("running", "ok") : pill("stopped")} · Storage used ${gb(st.storage.studio_bytes)} · Disk free ${gb(st.storage.disk_free_bytes)} ${st.storage.warning ? pill("LOW DISK") : ""}</div>
   <table style="margin-top:8px">${st.sessions.map((s) => `<tr><td>#${s.id} ${esc(s.stream)}</td><td>${pill(s.status)}</td><td class="small muted">${esc(s.status_detail || "")}</td></tr>`).join("")}</table>
   ${st.failed_jobs.length ? `<h3 style="margin-top:12px">Failed jobs</h3>${st.failed_jobs.map((j) => `<div class="small"><b>#${j.id} ${esc(j.kind)}</b> <span class="muted">${esc(j.error)}</span></div>`).join("")}` : ""}
   <div class="row" style="margin-top:10px"><button class="ghost small" onclick="act(()=>api('/api/maintenance/retention',{method:'POST'}),'Retention pass done')">Run data retention now</button></div></div>
   <div class="card"><h3>Jobs</h3><div class="chatlog">${jobsL.map((j) => `<div>#${j.id} ${esc(j.kind)} ${pill(j.status)} <span class="small muted">${esc(j.message || "")}</span></div>`).join("")}</div></div></div>
  <div class="card" style="margin-top:16px"><h3>Audit log <span class="small muted">security-relevant actions are marked</span></h3><div class="chatlog">${auditL.map((x) => `<div><span class="muted small">${fmtD(x.at)}</span> ${x.severity === "security" ? pill("security", "sim") : ""} <b>${esc(x.action)}</b> ${esc(x.target || "")} <span class="small muted">${esc(JSON.stringify(x.detail)).slice(0, 160)}</span></div>`).join("")}</div></div>`;
};
async function saveAutomation() {
  const patch = {};
  document.querySelectorAll("[data-a]").forEach((el) => (patch[el.dataset.a] = el.type === "checkbox" ? el.checked : Number(el.value)));
  patch.monitoring_window = { start: $("[data-w=start]").value, end: $("[data-w=end]").value };
  patch.narrator_preset = { provider: $("[data-n=provider]").value, voice: "Brian", speed: Number($("[data-n=speed]").value) };
  patch.score_weights = {}; document.querySelectorAll("[data-sw]").forEach((el) => (patch.score_weights[el.dataset.sw] = Number(el.value)));
  try { patch.keyword_weights = JSON.parse($("#kw").value || "{}"); } catch { return toast("Keyword weights must be JSON"); }
  if (patch.buffer_minutes < 2 || patch.buffer_minutes > 30) return toast("Buffer must be 2–30 minutes");
  await act(() => api("/api/automation", { method: "PUT", body: patch }), "Settings saved (new sessions use them)");
}
async function saveKeys() {
  const body = {};
  document.querySelectorAll("[data-key]").forEach((el) => { const v = el.value.trim(); if (el.type !== "password" || v) body[el.dataset.key] = v; });
  await act(() => api("/api/keys", { method: "PUT", body }), "Keys saved on this PC"); VIEWS.settings();
}
async function uploadOauth() {
  const f = $("#oauthf").files[0]; if (!f) return toast("Choose the client_secret_….json file first");
  const fd = new FormData(); fd.append("file", f);
  await act(() => api("/api/keys/oauth-client", { method: "POST", body: fd }), "OAuth client saved. Now click Connect YouTube channel."); VIEWS.settings();
}
async function savePreset(btn) { const tr = btn.closest("tr"); const v = {}; tr.querySelectorAll("[data-k]").forEach((el) => (v[el.dataset.k] = el.tagName === "SELECT" ? el.value : Number(el.value))); await act(() => api(`/api/presets/${encodeURIComponent(tr.dataset.p)}`, { method: "PUT", body: v }), "Preset saved"); }

if (!location.search.includes("static=1")) connectEvents(); // static=1: no live stream (screenshots, slow links)
loadBell(); route();
