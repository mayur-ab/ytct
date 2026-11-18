const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (s) => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : String(m).padStart(2, "0")) + ":" + String(x).padStart(2, "0"); };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 1800);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Copy failed"); }
}

// ------------------------------------------------------------------ status / settings
let STATUS = {};
async function loadStatus() {
  STATUS = await api("/api/status");
  const pill = $("#keyPill");
  if (!STATUS.ffmpeg) { pill.textContent = "ffmpeg missing"; pill.className = "pill warn"; }
  else if (STATUS.provider) {
    pill.textContent = `${STATUS.provider === "anthropic" ? "Claude" : "Groq"} ✓ ${STATUS.model}`; pill.className = "pill ok";
  } else { pill.textContent = "No API key set"; pill.className = "pill warn"; }
}

$("#settingsBtn").onclick = () => {
  const hint = (set, h, def) => (set ? `current key ${h} (leave empty to keep)` : def);
  $("#setProvider").value = STATUS.provider_setting || "auto";
  $("#setAnthropicKey").value = "";
  $("#setAnthropicKey").placeholder = hint(STATUS.anthropic_key_set, STATUS.anthropic_key_hint, "sk-ant-…");
  $("#setAnthropicModel").value = STATUS.anthropic_model || "";
  $("#setKey").value = "";
  $("#setKey").placeholder = hint(STATUS.groq_key_set, STATUS.groq_key_hint, "gsk_…");
  $("#setModel").value = STATUS.groq_model || "";
  $("#setWhisper").value = STATUS.whisper_model || "";
  $("#setOut").textContent = STATUS.output_dir || "";
  $("#settingsDlg").showModal();
};
$("#settingsDlg").addEventListener("close", async () => {
  if ($("#settingsDlg").returnValue !== "save") return;
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({
      provider: $("#setProvider").value,
      anthropic_api_key: $("#setAnthropicKey").value, anthropic_model: $("#setAnthropicModel").value,
      groq_api_key: $("#setKey").value, groq_model: $("#setModel").value, whisper_model: $("#setWhisper").value }) });
    await loadStatus(); toast("Settings saved");
  } catch (e) { toast(e.message); }
});

// ------------------------------------------------------------------ jobs
const openLogs = new Set();
let pollTimer = null;

$("#jobForm").onsubmit = async (e) => {
  e.preventDefault();
  const err = $("#formError"); err.classList.add("hidden");
  const options = {
    clip_seconds: $("#clipSeconds").value, mode: $("#mode").value, transcriber: $("#transcriber").value,
    max_clips: $("#maxClips").value || 0, start: $("#start").value || 0, end: $("#end").value || 0,
    metadata: $("#metadata").checked,
  };
  try {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ url: $("#url").value, options }) });
    $("#url").value = ""; openLogs.add(job.id); pollJobs();
  } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
};

async function pollJobs() {
  clearTimeout(pollTimer);
  let list = [];
  try { list = await api("/api/jobs"); } catch { /* server restarting */ }
  // fetch full logs only for expanded jobs
  const full = await Promise.all(list.map((j) => openLogs.has(j.id) ? api(`/api/jobs/${j.id}`).catch(() => j) : j));
  renderJobs(full);
  const active = full.some((j) => j.status === "running" || j.status === "queued");
  if (active || pollJobs.wasActive) { loadProjects(); refreshOpenProject(full); }
  pollJobs.wasActive = active;
  pollTimer = setTimeout(pollJobs, active ? 1200 : 5000);
}

function renderJobs(list) {
  $("#jobsCard").classList.toggle("hidden", !list.length);
  $("#jobs").innerHTML = list.map((j) => `
    <div class="job ${j.status}">
      <div class="job-top">
        <div style="min-width:0">
          <div class="job-title">${esc(j.title || j.url)}</div>
          <div class="job-stage">${esc(j.error || j.stage)}</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <span class="status ${j.status}">${j.status}</span>
          ${j.project_id ? `<button class="btn sm" data-open="${esc(j.project_id)}">View</button>` : ""}
          ${["running", "queued"].includes(j.status) ? `<button class="btn sm" data-cancel="${j.id}">Cancel</button>` : ""}
        </div>
      </div>
      <div class="bar"><div style="width:${(j.status === "done" ? 1 : j.progress) * 100}%"></div></div>
      <details data-job="${j.id}" ${openLogs.has(j.id) ? "open" : ""}>
        <summary>Log</summary>
        <div class="log">${esc((j.logs || []).join("\n"))}</div>
      </details>
    </div>`).join("");
  document.querySelectorAll(".job .log").forEach((el) => (el.scrollTop = el.scrollHeight));
}

$("#jobs").addEventListener("click", async (e) => {
  const open = e.target.dataset.open, cancel = e.target.dataset.cancel;
  if (open) location.hash = `#/p/${open}`;
  if (cancel) { await api(`/api/jobs/${cancel}/cancel`, { method: "POST" }); pollJobs(); }
});
$("#jobs").addEventListener("toggle", (e) => {
  const id = e.target.dataset?.job; if (!id) return;
  if (e.target.open) { if (!openLogs.has(id)) { openLogs.add(id); pollJobs(); } } else openLogs.delete(id);
}, true);

// ------------------------------------------------------------------ projects
async function loadProjects() {
  const list = await api("/api/projects").catch(() => []);
  $("#projects").innerHTML = list.length ? list.map((p) => `
    <div class="project" data-id="${esc(p.id)}">
      <img src="${esc(p.thumbnail || "")}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
      <div style="min-width:0">
        <div class="t">${esc(p.title)}</div>
        <div class="muted small">${p.count} shorts · ${fmt(p.duration)} · ${esc(p.status)}</div>
      </div>
    </div>`).join("") : `<div class="empty">No projects yet — paste a YouTube URL above.</div>`;
}
$("#projects").addEventListener("click", (e) => {
  const el = e.target.closest(".project"); if (el) location.hash = `#/p/${el.dataset.id}`;
});

let CURRENT = null;
async function showProject(id) {
  $("#homeView").classList.add("hidden"); $("#projectView").classList.remove("hidden");
  try { CURRENT = await api(`/api/projects/${encodeURIComponent(id)}`); }
  catch { toast("Project not found"); location.hash = ""; return; }
  $("#pTitle").textContent = CURRENT.title;
  $("#pMeta").innerHTML = `${esc(CURRENT.channel)} · ${fmt(CURRENT.duration)} · ${CURRENT.shorts.length} shorts · ` +
    `<a href="${esc(CURRENT.url)}" target="_blank" rel="noopener">source</a>`;
  renderShorts();
}

async function refreshOpenProject(jobsList) {
  if (!CURRENT || $("#projectView").classList.contains("hidden")) return;
  const job = jobsList.find((j) => j.project_id === CURRENT.id);
  if (!job) return;
  const fresh = await api(`/api/projects/${encodeURIComponent(CURRENT.id)}`).catch(() => null);
  if (!fresh) return;
  const sig = (p) => JSON.stringify(p.shorts.map((s) => [s.index, s.exists, !!s.metadata, s.transcript.length, s.error]));
  if (sig(fresh) !== sig(CURRENT)) { CURRENT = fresh; renderShorts(); }
}

function scoreClass(n) { return n >= 7 ? "hi" : n >= 5 ? "mid" : "lo"; }

function renderShorts() {
  const shorts = [...CURRENT.shorts];
  if ($("#sortBy").value === "score") shorts.sort((a, b) => (b.metadata?.virality_score || 0) - (a.metadata?.virality_score || 0));
  const base = `/files/${encodeURIComponent(CURRENT.id)}/`;
  $("#shorts").innerHTML = shorts.map((s) => {
    const m = s.metadata || {};
    const desc = m.description || "";
    return `
    <div class="short" data-index="${s.index}">
      ${s.exists ? `<video src="${base}${s.video}" controls preload="metadata" playsinline></video>` : `<div class="muted">not rendered</div>`}
      <div class="short-body">
        <div class="short-head">
          <span class="time">#${s.index} · ${fmt(s.start)}–${fmt(s.end)}</span>
          ${m.virality_score ? `<span class="score ${scoreClass(m.virality_score)}" title="${esc(m.reason)}">${m.virality_score}/10</span>` : ""}
        </div>
        ${s.error ? `<div class="error">${esc(s.error)}</div>` : ""}
        ${m.title ? `
          <div class="field"><div class="k">Title <button class="copy" data-copy="title">copy</button></div><div class="v title">${esc(m.title)}</div></div>
          <div class="field"><div class="k">Description <button class="copy" data-copy="description">copy</button></div><div class="v">${esc(desc)}</div></div>
          <div class="field"><div class="k">Tags <button class="copy" data-copy="tags">copy</button></div>
            <div class="tags">${(m.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div></div>
          ${m.reason ? `<div class="reason">${esc(m.reason)}</div>` : ""}`
        : `<div class="muted small">${STATUS.provider ? "No metadata yet." : "Set an Anthropic or Groq key in Settings, then click Generate."}</div>`}
        <details><summary>Transcript</summary><p>${esc(s.transcript || "(no transcript)")}</p></details>
        <div class="short-actions">
          <button class="btn sm" data-regen="${s.index}">${m.title ? "Regenerate" : "Generate"}</button>
          ${m.title ? `<button class="btn sm" data-copy="all">Copy all</button>` : ""}
          <a class="btn sm" href="${base}${s.video}" download>Download</a>
          <button class="btn sm" data-folder="${esc(s.dir)}">Folder</button>
        </div>
      </div>
    </div>`;
  }).join("") || `<div class="empty">No shorts yet — they will appear here as they are processed.</div>`;
}

$("#shorts").addEventListener("click", async (e) => {
  const card = e.target.closest(".short"); if (!card) return;
  const s = CURRENT.shorts.find((x) => x.index === Number(card.dataset.index));
  const m = s.metadata || {};
  const what = e.target.dataset.copy;
  if (what === "title") copy(m.title);
  if (what === "description") copy(m.description);
  if (what === "tags") copy((m.tags || []).join(", "));
  if (what === "all") copy(`${m.title}\n\n${m.description}\n\nTags: ${(m.tags || []).join(", ")}`);
  if (e.target.dataset.folder) api(`/api/projects/${encodeURIComponent(CURRENT.id)}/open?sub=${encodeURIComponent(e.target.dataset.folder)}`, { method: "POST" });
  if (e.target.dataset.regen) {
    const btn = e.target; btn.disabled = true; btn.textContent = "Working…";
    try {
      s.metadata = await api(`/api/projects/${encodeURIComponent(CURRENT.id)}/shorts/${s.index}/regenerate`, { method: "POST" });
      renderShorts(); toast("Metadata updated");
    } catch (ex) { toast(ex.message); btn.disabled = false; btn.textContent = "Retry"; }
  }
});

$("#sortBy").onchange = renderShorts;
$("#openFolderBtn").onclick = () => api(`/api/projects/${encodeURIComponent(CURRENT.id)}/open`, { method: "POST" });
$("#backBtn").onclick = () => (location.hash = "");
$("#homeLink").onclick = (e) => { e.preventDefault(); location.hash = ""; };

// ------------------------------------------------------------------ routing
function route() {
  const m = location.hash.match(/^#\/p\/(.+)$/);
  if (m) showProject(decodeURIComponent(m[1]));
  else { CURRENT = null; $("#projectView").classList.add("hidden"); $("#homeView").classList.remove("hidden"); loadProjects(); }
}
window.addEventListener("hashchange", route);

loadStatus(); route(); pollJobs();
