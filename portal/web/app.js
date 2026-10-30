if (new URLSearchParams(location.search).get("theme") === "console") {
  document.documentElement.classList.add("theme-console");
}
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  config: null,
  signalFile: null,
  musicFiles: [],
  musicExcavFiles: [],
  photoFiles: [],
  photoUrls: [],
  jobs: [],
  activeJob: null,
  activeTimer: null,
  uploadTimer: null,
  busy: { signal: false, music: false, musicExcav: false, photos: false },
  genesis: null,
  toastTimer: null,
};

const STATUS = {
  uploading: "UPLOADING",
  queued: "QUEUED",
  running: "PUBLISHING",
  published: "LIVE",
  staged: "STAGED",
  error: "FAILED",
};

const KIND = { signal: "SIGNAL", music: "MUSIC", photo: "PHOTO" };

const fmtBytes = (n) => {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
};

const fmtNum = (n) => Number(n || 0).toLocaleString("en-US");

const shortRepo = (path) => {
  if (!path) return "—";
  const parts = String(path).replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.slice(-2).join("/");
};

const fmtAgo = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const sec = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
};

const statusLabel = (s) => STATUS[s] || String(s || "UNKNOWN").toUpperCase();

const statusClass = (s) => {
  if (s === "published" || s === "staged") return "ok";
  if (s === "error") return "bad";
  if (s === "running" || s === "queued" || s === "uploading") return "warn";
  return "";
};

async function api(url, opts) {
  const response = await fetch(url, opts);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value == null ? "—" : String(value);
}

function setHTML(id, value) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = value;
}

function setNotice(id, text, error = false) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("err", !!error);
}

function toast(text, error = false) {
  const el = $("#toast");
  if (!el) return;
  clearTimeout(state.toastTimer);
  el.textContent = text;
  el.classList.toggle("err", !!error);
  el.classList.add("show");
  state.toastTimer = setTimeout(() => el.classList.remove("show"), 3800);
}

function setHealth(kind, text, sub) {
  const pill = $("#health-pill");
  const label = $("#health-label");
  const title = $("#health-title");
  const subtitle = $("#health-sub");
  if (pill) pill.className = `status-pill ${kind}`;
  if (label) label.textContent = text;
  if (title) title.textContent = text;
  if (subtitle && sub) subtitle.textContent = sub;
}

function setWorkspaceState(text, dot = true) {
  const el = $("#workspace-state");
  if (!el) return;
  el.textContent = text;
  const parent = el.closest(".workspace-state");
  if (parent) parent.querySelector(".status-dot").style.display = dot ? "" : "none";
}

/* ---------- tabs ---------- */
function selectTab(name) {
  const kind = ["command", "agents", "signal", "music", "photos", "genesis"].includes(name) ? name : "command";
  $$(".tab").forEach((tab) => {
    const active = tab.dataset.tab === kind;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  $$("[data-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.panel !== kind;
  });
  $$("[data-tab-link]").forEach((link) => {
    link.classList.toggle("is-active-link", link.dataset.tabLink === kind);
  });
  const states = {
    command: "Commander HUD live",
    console: "LYGO Console 1.9 live",
    agents: "Agent Manager online",
    signal: "Signal intake ready",
    music: "Music intake ready",
    photos: "Data Vault ready",
    genesis: "Genesis HUD live",
  };
  setWorkspaceState(states[kind]);
  if (kind === "genesis") loadGenesis();
  if (kind === "agents") loadAgents();
  if (kind === "console") loadConsole();
  if (location.hash !== `#${kind}`) history.replaceState(null, "", `#${kind}`);
}

function initTabs() {
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => selectTab(tab.dataset.tab)));
  $$("[data-tab-link]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      selectTab(link.dataset.tabLink);
      $("#workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  const fromHash = location.hash.replace("#", "");
  selectTab(fromHash || "command");
}

/* ---------- jobs ---------- */
function openJob(job) {
  if (!job) return;
  state.activeJob = job;
  const dock = $("#job-dock");
  if (!dock) return;
  dock.hidden = false;
  setText("job-kind", KIND[job.kind] || String(job.kind || "JOB").toUpperCase());
  setText("job-title", job.filename || job.title || "Publishing job");
  renderJob(job);
  dock.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderJob(job) {
  if (!job) return;
  state.activeJob = job;
  const status = $("#job-status");
  if (status) {
    status.textContent = statusLabel(job.status);
    status.className = `job-status ${job.status || "queued"}`;
  }
  const progress = $("#job-progress");
  if (progress) {
    const pct = job.status === "queued" ? 10
      : job.status === "running" ? 64
      : job.status === "uploading" ? 28
      : 100;
    progress.style.width = `${pct}%`;
  }
  const log = $("#job-log");
  if (log) {
    log.textContent = (job.log || []).join("\n") || "Waiting for the importer…";
    log.scrollTop = log.scrollHeight;
  }
  setText("job-time", `${job.created ? fmtAgo(job.created) : "just now"}`);
  setText("job-repo", shortRepo(job.repo));
  const link = $("#job-link");
  if (link) {
    if (job.status === "published" && job.url) {
      link.hidden = false;
      link.href = job.url;
      link.innerHTML = `Open live result <svg class="icon"><use href="#i-external"/></svg>`;
    } else if (job.status === "staged") {
      link.hidden = false;
      link.href = "#";
      link.textContent = "STAGED — publish disabled";
    } else {
      link.hidden = true;
    }
  }
}

function finishJob(job) {
  state.busy[job.kind === "photo" ? "photos" : job.kind] = false;
  loadJobs();
  loadOverview();
  if (job.status === "published") {
    toast(`${KIND[job.kind] || "Job"} published. Live result is ready.`);
  } else if (job.status === "staged") {
    toast("Job staged without push. Publish is disabled in this portal.");
  } else if (job.status === "error") {
    toast(job.error || "The importer failed. Open the log for the traceback.", true);
  }
  renderJob(job);
}

function trackJob(id) {
  clearInterval(state.activeTimer);
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      renderJob(job);
      if (!["queued", "running", "uploading"].includes(job.status)) {
        clearInterval(state.activeTimer);
        state.activeTimer = null;
        finishJob(job);
      }
    } catch (error) {
      const log = $("#job-log");
      if (log) log.textContent += `\n[portal] ${error.message}`;
    }
  };
  tick();
  state.activeTimer = setInterval(tick, 1000);
}

function renderJobs() {
  const box = $("#jobs-list");
  if (!box) return;
  const jobs = state.jobs || [];
  const active = jobs.filter((job) => ["queued", "running", "uploading"].includes(job.status)).length;
  setText("activity-count", active);
  if (!jobs.length) {
    box.innerHTML = '<p class="empty">No jobs yet. The next drop starts the queue.</p>';
    return;
  }
  box.innerHTML = "";
  for (const job of jobs.slice(0, 7)) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "job-row";
    row.title = job.filename || "Job";
    row.innerHTML = `
      <span class="job-row-dot ${job.status || ""}"></span>
      <span class="job-row-main">
        <span class="job-row-name"></span>
        <span class="job-row-sub">${KIND[job.kind] || "JOB"} · ${job.created ? fmtAgo(job.created) : ""}</span>
      </span>
      <span class="job-row-status ${job.status || ""}">${statusLabel(job.status)}</span>`;
    $(".job-row-name", row).textContent = job.filename || job.title || "Untitled job";
    row.addEventListener("click", () => openJob(job));
    box.appendChild(row);
  }
}

async function loadJobs() {
  try {
    const data = await api("/api/jobs");
    state.jobs = data.jobs || [];
    renderJobs();
  } catch (error) {
    const box = $("#jobs-list");
    if (box) box.innerHTML = `<p class="empty">${error.message}</p>`;
  }
}

async function clearFinishedJobs() {
  try {
    const result = await api("/api/jobs/clear", { method: "POST" });
    toast(result.removed ? `Cleared ${result.removed} finished job${result.removed === 1 ? "" : "s"}.` : "No finished jobs to clear.");
    if (state.activeJob && !["queued", "running", "uploading"].includes(state.activeJob.status)) {
      state.activeJob = null;
      $("#job-dock").hidden = true;
    }
    loadJobs();
  } catch (error) {
    toast(error.message, true);
  }
}

/* ---------- upload dock ---------- */
function showWorkingDock(kind, filename) {
  const job = { id: "uploading", kind, filename, status: "uploading", log: ["Uploading files…"], created: new Date().toISOString() };
  openJob(job);
}

function setUploadStatus(text, progress) {
  const log = $("#job-log");
  if (log) log.textContent = text;
  const bar = $("#job-progress");
  if (bar && progress != null) bar.style.width = `${progress}%`;
}

/* ---------- episodes ---------- */
function renderEpisodes(data) {
  setText("stat-episodes", fmtNum(data.total));
  setText("stat-shows", fmtNum(data.total));
  setText("stat-radio", fmtNum(data.radio));
  const box = $("#latest-episodes");
  if (!box) return;
  if (!data.episodes || !data.episodes.length) {
    box.innerHTML = '<p class="empty">No episodes found.</p>';
    return;
  }
  box.innerHTML = "";
  for (const ep of data.episodes.slice(0, 6)) {
    const link = document.createElement("a");
    link.className = "latest-item";
    link.href = `https://excavationpro.ca/signal/${ep.slug}/`;
    link.target = "_blank";
    link.rel = "noopener";
    link.innerHTML = `<span class="latest-title"></span><span class="latest-meta">${ep.duration || "—"} · ${ep.desk ? "studio desk" : "AI Radio"}</span>`;
    $(".latest-title", link).textContent = ep.title || ep.slug;
    box.appendChild(link);
  }
}

/* ---------- overview / health ---------- */
async function loadConfig() {
  try {
    const cfg = await api("/api/config");
    state.config = cfg;
    setText("repo-signal", shortRepo(cfg.repo));
    setText("repo-music", shortRepo(cfg.music_repo));
    setText("repo-photo", shortRepo(cfg.photo_repo));
    if (!cfg.publish) {
      setText("mode-label", "STAGE ONLY");
      $("#mode-label").style.color = "var(--gold)";
    }
  } catch (error) {
    setHealth("is-dead", "Portal unreachable", error.message);
  }
}

async function loadOverview() {
  const requests = [
    api("/api/episodes"),
    api("/api/music/info"),
    api("/api/photo/info"),
    api("/api/genesis"),
  ];
  const [episodes, music, photos, genesis] = await Promise.allSettled(requests);
  let good = 0;
  if (episodes.status === "fulfilled") { renderEpisodes(episodes.value); good++; }
  if (music.status === "fulfilled") { setText("stat-music", fmtNum(music.value.tracks)); good++; }
  if (photos.status === "fulfilled") { setText("stat-photos", fmtNum(photos.value.items)); good++; }
  if (genesis.status === "fulfilled") { renderGenesis(genesis.value); good++; }
  setText("last-check", new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));

  try {
    const health = await api("/health");
    if (health.ok && good >= 3) {
      setHealth("is-live", "Portal healthy", "Content feeds live · queue ready");
    } else if (health.ok) {
      setHealth("is-partial", "Partial telemetry", "One or more content feeds did not answer");
    } else {
      setHealth("is-dead", "Portal unhealthy", "Health endpoint did not report OK");
    }
  } catch (error) {
    setHealth("is-dead", "Portal unreachable", error.message);
  }
}

/* ---------- genesis HUD ---------- */
function statusPill(el, label, cls) {
  if (!el) return;
  el.textContent = label;
  el.className = `mini-status ${cls || ""}`;
}

function renderGenesis(g) {
  state.genesis = g;
  if (!g) return;
  const wallet = g.wallet || {};
  if (wallet.available) {
    setText("gen-balance", Number(wallet.balance_eth || 0).toFixed(6));
    setText("gen-gas", wallet.gas_gwei == null ? "—" : Number(wallet.gas_gwei).toFixed(2));
    setText("gen-wallet-state", "LIVE RPC");
    $("#gen-wallet-state").style.color = "var(--green)";
  } else {
    setText("gen-balance", "—");
    setText("gen-gas", "—");
    setText("gen-wallet-state", "RPC ERROR");
    $("#gen-wallet-state").style.color = "var(--red)";
  }
  setText("gen-address", wallet.address || "—");
  const scan = $("#gen-basescan");
  if (scan && wallet.address) scan.href = `https://basescan.org/address/${wallet.address}`;

  const clawnch = g.clawnch || {};
  setText("gen-clawnch", (clawnch.status || "unknown").toUpperCase());
  setText("gen-pid", clawnch.pid || "—");
  setText("gen-clawnch-detail", clawnch.detail || "No process detail");
  const clawPill = $("#gen-clawnch-pill");
  statusPill(clawPill, (clawnch.status || "unknown").toUpperCase(), clawnch.status === "running" ? "ok" : clawnch.status === "stopped" ? "bad" : "warn");

  const token = g.token || {};
  setText("gen-token", token.available ? (token.symbol || "REGISTERED") : "NO DATA");
  setText("gen-token-symbol", token.available ? (token.symbol || "—") : "—");
  setText("gen-token-status", token.available ? (token.status || "—") : "—");
  setText("gen-token-launched", token.available ? (token.launched_at || "—") : "—");
  setText("gen-token-contract", token.available ? (token.contract || "—") : "No registry file");
  const tokenPill = $("#gen-token");
  if (tokenPill) tokenPill.style.color = token.available ? "var(--gold)" : "var(--dim)";

  const services = g.services || [];
  const up = services.filter((s) => s.up).length;
  setText("gen-service-count", `${up}/${services.length} UP`);
  const serviceBox = $("#gen-services");
  if (serviceBox) {
    serviceBox.innerHTML = "";
    for (const service of services) {
      const row = document.createElement("div");
      row.className = "service-row";
      row.innerHTML = `<span class="service-dot ${service.up ? "up" : "down"}"></span>
        <span class="service-name"></span>
        <span class="service-port">:${service.port}</span>`;
      $(".service-name", row).textContent = service.name;
      serviceBox.appendChild(row);
    }
  }

  const system = g.system || {};
  setText("gen-host", system.host || "—");
  setText("gen-uptime", system.uptime || "—");
  setText("gen-cpu", system.cpu == null ? "—" : `${system.cpu}%`);
  setText("gen-memory", system.memory == null ? "—" : `${system.memory}%`);
  setText("gen-disk", system.disk == null ? "—" : `${system.disk}%`);

  const audit = g.audit || {};
  const auditPill = $("#gen-audit-status");
  const auditRing = $(".audit-ring");
  const auditLabel = $("#gen-audit-label");
  if (audit.available) {
    const coherent = String(audit.status).toUpperCase() === "COHERENT";
    statusPill(auditPill, String(audit.status || "UNKNOWN").toUpperCase(), coherent ? "ok" : "bad");
    if (auditRing) auditRing.classList.toggle("bad", !coherent);
    if (auditLabel) auditLabel.textContent = coherent ? "Coherent" : "Review required";
    setText("gen-audit-meta", `LAST AUDIT · ${audit.findings || 0} findings · ${audit.timestamp || "timestamp unavailable"}${audit.drift_avg != null ? ` · drift ${audit.drift_avg.toFixed(2)}` : ""}`);
  } else {
    statusPill(auditPill, "NO REPORT", "warn");
    if (auditRing) auditRing.classList.add("bad");
    if (auditLabel) auditLabel.textContent = "No audit report";
    setText("gen-audit-meta", "Audit_Report.json is unavailable; no score is being invented.");
  }
  setText("gen-updated", `UPDATED ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
  setNotice("genesis-notice", "");
}

async function loadGenesis() {
  try {
    const data = await api("/api/genesis");
    renderGenesis(data);
  } catch (error) {
    setNotice("genesis-notice", `Genesis HUD unavailable: ${error.message}`, true);
    setText("gen-updated", "FEED ERROR");
  }
}

/* ---------- music / photo file helpers ---------- */
function isAudioFile(name) { return /\.(mp3|wav|flac|m4a)$/i.test(name || ""); }
function isImageFile(name) { return /\.(jpe?g|png|webp|gif|bmp|tiff?)$/i.test(name || ""); }

async function readDirectory(reader) {
  const out = [];
  while (true) {
    const batch = await new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
    if (!batch.length) break;
    for (const entry of batch) {
      if (entry.isFile) {
        const file = await new Promise((resolve) => entry.file(resolve, () => resolve(null)));
        if (file) out.push({ file, rel: entry.fullPath.replace(/^\/+/, "") });
      } else if (entry.isDirectory) {
        out.push(...await readDirectory(entry.createReader()));
      }
    }
  }
  return out;
}

async function filesFromDrop(dt) {
  const items = [...(dt.items || [])];
  const entries = items.map((item) => item.webkitGetAsEntry && item.webkitGetAsEntry()).filter(Boolean);
  if (entries.length) {
    const out = [];
    for (const entry of entries) {
      if (entry.isFile) {
        const file = await new Promise((resolve) => entry.file(resolve, () => resolve(null)));
        if (file) out.push({ file, rel: file.name });
      } else if (entry.isDirectory) {
        out.push(...await readDirectory(entry.createReader()));
      }
    }
    return out;
  }
  return [...(dt.files || [])].map((file) => ({ file, rel: file.webkitRelativePath || file.name }));
}

function wireDrop(zone, input, onFiles) {
  if (!zone || !input) return;
  zone.addEventListener("click", (event) => {
    if (event.target.closest("button")) return;
    input.click();
  });
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
  });
  for (const ev of ["dragenter", "dragover"]) {
    zone.addEventListener(ev, (event) => { event.preventDefault(); zone.classList.add("over"); });
  }
  for (const ev of ["dragleave", "drop"]) {
    zone.addEventListener(ev, (event) => { event.preventDefault(); zone.classList.remove("over"); });
  }
  zone.addEventListener("drop", async (event) => {
    try { onFiles(await filesFromDrop(event.dataTransfer)); }
    catch { onFiles([{ file: event.dataTransfer.files[0], rel: event.dataTransfer.files[0]?.name || "file" }]); }
  });
}

/* ---------- signal ---------- */
function signalSet(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".zip")) { setNotice("signal-notice", "That is not a ZIP. Drop an episode bundle.", true); return; }
  if (file.size > 120 * 1024 * 1024) { setNotice("signal-notice", "That ZIP is over 120 MB.", true); return; }
  state.signalFile = file;
  setText("signal-name", file.name);
  setText("signal-size", `${fmtBytes(file.size)} · ready`);
  $("#signal-selection").hidden = false;
  setNotice("signal-notice", "");
  toast("Episode bundle ready.");
}

function signalClear() {
  state.signalFile = null;
  $("#signal-file").value = "";
  $("#signal-selection").hidden = true;
  setNotice("signal-notice", "");
}

async function publishSignal() {
  if (state.busy.signal) return;
  if (!state.signalFile) { setNotice("signal-notice", "Choose a ZIP first.", true); return; }
  state.busy.signal = true;
  const button = $("#signal-publish");
  button.disabled = true;
  const file = state.signalFile;
  showWorkingDock("signal", file.name);
  setNotice("signal-notice", "Uploading and validating the bundle…");
  try {
    const response = await fetch("/api/upload", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) },
      body: file,
    });
    const job = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);
    signalClear();
    setNotice("signal-notice", "");
    renderJob(job);
    trackJob(job.id);
    loadJobs();
  } catch (error) {
    state.busy.signal = false;
    setNotice("signal-notice", error.message, true);
    $("#job-dock").hidden = true;
    button.disabled = false;
  }
}

function initSignal() {
  const drop = $("#signal-drop");
  const input = $("#signal-file");
  wireDrop(drop, input, (files) => signalSet(files[0]?.file));
  input.addEventListener("change", () => signalSet(input.files[0]));
  $("#signal-clear").addEventListener("click", signalClear);
  $("#signal-publish").addEventListener("click", publishSignal);
}

/* ---------- music ---------- */
function musicSetFiles(items) {
  const seen = new Set();
  const files = [];
  for (const item of items) {
    const rel = (item.rel || item.file.name).replace(/\\/g, "/");
    if (!isAudioFile(rel)) continue;
    const key = `${rel}|${item.file.size}`;
    if (seen.has(key)) continue;
    seen.add(key);
    files.push({ file: item.file, rel });
  }
  state.musicFiles = files;
  const selection = $("#music-selection");
  const clear = $("#music-clear");
  const publish = $("#music-publish");
  if (!files.length) {
    setText("music-count", "No songs selected");
    setText("music-total", "Drop files to begin");
    if (selection) selection.hidden = true;
    if (clear) clear.hidden = true;
    if (publish) publish.disabled = true;
    setNotice("music-notice", "No MP3/WAV/FLAC/M4A files in that drop.", true);
    return;
  }
  const total = files.reduce((sum, item) => sum + item.file.size, 0);
  setText("music-count", `${files.length} song${files.length === 1 ? "" : "s"} selected`);
  setText("music-total", `${fmtBytes(total)} · ready to publish`);
  if (clear) clear.hidden = false;
  if (publish) publish.disabled = false;
  if (selection) {
    selection.hidden = false;
    selection.innerHTML = "";
    for (const item of files.slice(0, 5)) {
      const chip = document.createElement("span");
      chip.className = "file-chip";
      chip.innerHTML = '<svg class="icon"><use href="#i-music"/></svg><span></span>';
      $("span", chip).textContent = item.rel;
      selection.appendChild(chip);
    }
    if (files.length > 5) {
      const chip = document.createElement("span");
      chip.className = "file-chip";
      chip.textContent = `+${files.length - 5} more`;
      selection.appendChild(chip);
    }
  }
  setNotice("music-notice", "");
}

function musicClear() {
  state.musicFiles = [];
  $("#music-files").value = "";
  $("#music-folder").value = "";
  $("#music-selection").hidden = true;
  $("#music-clear").hidden = true;
  $("#music-publish").disabled = true;
  setText("music-count", "No songs selected");
  setText("music-total", "Drop files to begin");
  setNotice("music-notice", "");
}

async function publishMusic() {
  if (state.busy.music) return;
  if (!state.musicFiles.length) { setNotice("music-notice", "Choose songs or a folder first.", true); return; }
  state.busy.music = true;
  const button = $("#music-publish");
  button.disabled = true;
  const files = state.musicFiles;
  showWorkingDock("music", files.length === 1 ? files[0].rel : `${files.length} songs`);
  setNotice("music-notice", "Opening music job…");
  try {
    const start = await api("/api/music/start", { method: "POST" });
    const id = start.id;
    for (let i = 0; i < files.length; i++) {
      const item = files[i];
      setNotice("music-notice", `Uploading ${i + 1}/${files.length}: ${item.rel}`);
      setUploadStatus(`Uploading ${i + 1}/${files.length} — ${item.rel}`, 10 + Math.round(((i + 1) / files.length) * 55));
      const response = await fetch(`/api/music/file?job=${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Relpath": encodeURIComponent(item.rel) },
        body: item.file,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `upload HTTP ${response.status}`);
      }
    }
    setNotice("music-notice", "Upload complete. Queueing import…");
    const job = await api("/api/music/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, album: $("#music-album").value.trim(), artist: "Excavationpro" }),
    });
    musicClear();
    renderJob(job);
    trackJob(id);
    loadJobs();
  } catch (error) {
    state.busy.music = false;
    button.disabled = false;
    setNotice("music-notice", error.message, true);
    $("#job-status").textContent = "FAILED";
    $("#job-status").className = "job-status error";
  }
}

function initMusic() {
  const filesInput = $("#music-files");
  const folderInput = $("#music-folder");
  wireDrop($("#music-drop"), filesInput, (files) => musicSetFiles(files));
  $("#music-choose-files").addEventListener("click", (event) => { event.stopPropagation(); filesInput.click(); });
  $("#music-choose-folder").addEventListener("click", (event) => { event.stopPropagation(); folderInput.click(); });
  filesInput.addEventListener("change", () => musicSetFiles([...filesInput.files].map((file) => ({ file, rel: file.name }))));
  folderInput.addEventListener("change", () => musicSetFiles([...folderInput.files].map((file) => ({ file, rel: file.webkitRelativePath || file.name }))));
  $("#music-clear").addEventListener("click", musicClear);
  $("#music-publish").addEventListener("click", publishMusic);
}

/* ---------- ExcavationPro music ---------- */
function musicExcavSetFiles(items) {
  const seen = new Set();
  const files = [];
  for (const item of items) {
    const rel = (item.rel || item.file.name).replace(/\\/g, "/");
    if (!isAudioFile(rel)) continue;
    const key = `${rel}|${item.file.size}`;
    if (seen.has(key)) continue;
    seen.add(key);
    files.push({ file: item.file, rel });
  }
  state.musicExcavFiles = files;
  const selection = $("#music-excav-selection");
  const clear = $("#music-excav-clear");
  const publish = $("#music-excav-publish");
  if (!files.length) {
    setText("music-excav-count", "No songs selected");
    setText("music-excav-total", "Drop files to begin");
    if (selection) selection.hidden = true;
    if (clear) clear.hidden = true;
    if (publish) publish.disabled = true;
    setNotice("music-excav-notice", "No MP3/WAV/FLAC/M4A files in that drop.", true);
    return;
  }
  const total = files.reduce((sum, item) => sum + item.file.size, 0);
  setText("music-excav-count", `${files.length} song${files.length === 1 ? "" : "s"} selected`);
  setText("music-excav-total", `${fmtBytes(total)} · ready to publish`);
  if (clear) clear.hidden = false;
  if (publish) publish.disabled = false;
  if (selection) {
    selection.hidden = false;
    selection.innerHTML = "";
    for (const item of files.slice(0, 5)) {
      const chip = document.createElement("span");
      chip.className = "file-chip";
      chip.innerHTML = '<svg class="icon"><use href="#i-music"/></svg><span></span>';
      $("span", chip).textContent = item.rel;
      selection.appendChild(chip);
    }
  }
  setNotice("music-excav-notice", "");
}

function musicExcavClear() {
  state.musicExcavFiles = [];
  $("#music-excav-files").value = "";
  $("#music-excav-folder").value = "";
  $("#music-excav-selection").hidden = true;
  $("#music-excav-clear").hidden = true;
  $("#music-excav-publish").disabled = true;
  setText("music-excav-count", "No songs selected");
  setText("music-excav-total", "Drop files to begin");
  setNotice("music-excav-notice", "");
}

async function publishMusicExcav() {
  if (state.busy.musicExcav) return;
  if (!state.musicExcavFiles.length) { setNotice("music-excav-notice", "Choose songs or a folder first.", true); return; }
  state.busy.musicExcav = true;
  const button = $("#music-excav-publish");
  button.disabled = true;
  const files = state.musicExcavFiles;
  showWorkingDock("music", files.length === 1 ? files[0].rel : `${files.length} songs`);
  setNotice("music-excav-notice", "Opening ExcavationPro music job…");
  try {
    const start = await api("/api/music/start?target=excav", { method: "POST" });
    const id = start.id;
    for (let i = 0; i < files.length; i++) {
      const item = files[i];
      setNotice("music-excav-notice", `Uploading ${i + 1}/${files.length}: ${item.rel}`);
      setUploadStatus(`Uploading ${i + 1}/${files.length} — ${item.rel}`, 10 + Math.round(((i + 1) / files.length) * 55));
      const response = await fetch(`/api/music/file?job=${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Relpath": encodeURIComponent(item.rel) },
        body: item.file,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `upload HTTP ${response.status}`);
      }
    }
    setNotice("music-excav-notice", "Upload complete. Queueing import…");
    const job = await api("/api/music/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, album: $("#music-excav-album").value.trim(), artist: "Excavationpro" }),
    });
    musicExcavClear();
    renderJob(job);
    trackJob(id);
    loadJobs();
  } catch (error) {
    state.busy.musicExcav = false;
    button.disabled = false;
    setNotice("music-excav-notice", error.message, true);
    $("#job-status").textContent = "FAILED";
    $("#job-status").className = "job-status error";
  }
}

function initMusicExcav() {
  const filesInput = $("#music-excav-files");
  const folderInput = $("#music-excav-folder");
  wireDrop($("#music-excav-drop"), filesInput, (files) => musicExcavSetFiles(files));
  $("#music-excav-choose-files").addEventListener("click", (event) => { event.stopPropagation(); filesInput.click(); });
  $("#music-excav-choose-folder").addEventListener("click", (event) => { event.stopPropagation(); folderInput.click(); });
  filesInput.addEventListener("change", () => musicExcavSetFiles([...filesInput.files].map((file) => ({ file, rel: file.name }))));
  folderInput.addEventListener("change", () => musicExcavSetFiles([...folderInput.files].map((file) => ({ file, rel: file.webkitRelativePath || file.name }))));
  $("#music-excav-clear").addEventListener("click", musicExcavClear);
  $("#music-excav-publish").addEventListener("click", publishMusicExcav);
}

/* ---------- photos ---------- */
function photoSetFiles(items) {
  const seen = new Set();
  const files = [];
  for (const item of items) {
    const rel = (item.rel || item.file.name).replace(/\\/g, "/");
    if (!isImageFile(rel)) continue;
    const key = `${rel}|${item.file.size}`;
    if (seen.has(key)) continue;
    seen.add(key);
    files.push({ file: item.file, rel });
  }
  state.photoFiles = files;
  state.photoUrls.forEach((url) => URL.revokeObjectURL(url));
  state.photoUrls = [];
  const selection = $("#photo-previews");
  const clear = $("#photo-clear");
  const publish = $("#photo-publish");
  if (!files.length) {
    setText("photo-count", "No photos selected");
    setText("photo-total", "Drop files to begin");
    if (selection) selection.hidden = true;
    if (clear) clear.hidden = true;
    if (publish) publish.disabled = true;
    setNotice("photo-notice", "No JPG/PNG/WebP/GIF/BMP/TIFF files in that drop.", true);
    return;
  }
  const total = files.reduce((sum, item) => sum + item.file.size, 0);
  setText("photo-count", `${files.length} photo${files.length === 1 ? "" : "s"} selected`);
  setText("photo-total", `${fmtBytes(total)} · ready to publish`);
  if (clear) clear.hidden = false;
  if (publish) publish.disabled = false;
  if (selection) {
    selection.hidden = false;
    selection.innerHTML = "";
    for (const [index, item] of files.slice(0, 6).entries()) {
      const thumb = document.createElement("div");
      thumb.className = "photo-thumb";
      const url = URL.createObjectURL(item.file);
      state.photoUrls.push(url);
      const image = document.createElement("img");
      image.src = url;
      image.alt = item.rel;
      thumb.appendChild(image);
      if (index === 5 && files.length > 6) {
        const more = document.createElement("span");
        more.className = "photo-more";
        more.textContent = `+${files.length - 6}`;
        thumb.appendChild(more);
      }
      selection.appendChild(thumb);
    }
  }
  setNotice("photo-notice", "");
}

function photoClear() {
  state.photoFiles = [];
  state.photoUrls.forEach((url) => URL.revokeObjectURL(url));
  state.photoUrls = [];
  $("#photo-files").value = "";
  $("#photo-folder").value = "";
  $("#photo-previews").hidden = true;
  $("#photo-clear").hidden = true;
  $("#photo-publish").disabled = true;
  setText("photo-count", "No photos selected");
  setText("photo-total", "Drop files to begin");
  setNotice("photo-notice", "");
}

async function publishPhotos() {
  if (state.busy.photos) return;
  if (!state.photoFiles.length) { setNotice("photo-notice", "Choose a photo first.", true); return; }
  state.busy.photos = true;
  const button = $("#photo-publish");
  button.disabled = true;
  const files = state.photoFiles;
  showWorkingDock("photo", files.length === 1 ? files[0].rel : `${files.length} photos`);
  setNotice("photo-notice", "Opening photo job…");
  try {
    const start = await api("/api/photo/start", { method: "POST" });
    const id = start.id;
    for (let i = 0; i < files.length; i++) {
      const item = files[i];
      setNotice("photo-notice", `Uploading ${i + 1}/${files.length}: ${item.rel}`);
      setUploadStatus(`Uploading ${i + 1}/${files.length} — ${item.rel}`, 10 + Math.round(((i + 1) / files.length) * 55));
      const response = await fetch(`/api/photo/file?job=${encodeURIComponent(id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-Relpath": encodeURIComponent(item.rel) },
        body: item.file,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `upload HTTP ${response.status}`);
      }
    }
    setNotice("photo-notice", "Upload complete. Queueing gallery import…");
    const job = await api("/api/photo/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, title: $("#photo-title").value.trim(), tags: $("#photo-tags").value.trim() || "PHOTO" }),
    });
    photoClear();
    renderJob(job);
    trackJob(id);
    loadJobs();
  } catch (error) {
    state.busy.photos = false;
    button.disabled = false;
    setNotice("photo-notice", error.message, true);
    $("#job-status").textContent = "FAILED";
    $("#job-status").className = "job-status error";
  }
}

function initPhotos() {
  const filesInput = $("#photo-files");
  const folderInput = $("#photo-folder");
  wireDrop($("#photo-drop"), filesInput, (files) => photoSetFiles(files));
  $("#photo-choose-files").addEventListener("click", (event) => { event.stopPropagation(); filesInput.click(); });
  $("#photo-choose-folder").addEventListener("click", (event) => { event.stopPropagation(); folderInput.click(); });
  filesInput.addEventListener("change", () => photoSetFiles([...filesInput.files].map((file) => ({ file, rel: file.name }))));
  folderInput.addEventListener("change", () => photoSetFiles([...folderInput.files].map((file) => ({ file, rel: file.webkitRelativePath || file.name }))));
  $("#photo-clear").addEventListener("click", photoClear);
  $("#photo-publish").addEventListener("click", publishPhotos);
}

/* ---------- init ---------- */
/* ---------- commander ---------- */
function fmtDisk(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i > 2 ? 1 : 0)} ${units[i]}`;
}

function renderCommander(data) {
  const actions = data.actions || [];
  const bootBox = $("#boot-grid");
  if (bootBox) {
    bootBox.innerHTML = "";
    const groups = ["AGENTS", "LLMS", "SYSTEMS"];
    for (const group of groups) {
      const rows = actions.filter((action) => action.group === group);
      if (!rows.length) continue;
      const header = document.createElement("div");
      header.className = "boot-group";
      header.textContent = group;
      bootBox.appendChild(header);
      for (const action of rows) {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "boot-card";
        card.dataset.action = action.id;
        card.dataset.name = action.name;
        const ledClass = action.online === true ? "live" : action.online === false ? "down" : "";
        card.innerHTML = `
          <span class="boot-icon"><svg class="icon"><use href="#${action.kind === "url" ? "i-external" : "i-bolt"}"/></svg></span>
          <span class="boot-name"></span>
          <span class="boot-note"></span>
          <span class="boot-led ${ledClass}"></span>
          <span class="boot-go">LAUNCH <svg class="icon"><use href="#i-arrow"/></svg></span>`;
        $(".boot-name", card).textContent = action.name;
        $(".boot-note", card).textContent = action.note || "Local action";
        card.title = `${action.name} — ${action.path}`;
        bootBox.appendChild(card);
      }
    }
  }

  const folders = data.folders || [];
  const folderBox = $("#folder-grid");
  if (folderBox) {
    folderBox.innerHTML = "";
    for (const folder of folders) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "folder-card";
      card.dataset.action = folder.id;
      card.dataset.name = folder.name;
      card.title = folder.path;
      card.innerHTML = `
        <span class="folder-icon"><svg class="icon"><use href="#i-folder"/></svg></span>
        <span><span class="folder-name"></span><span class="folder-meta"></span><span class="folder-path"></span></span>
        <span class="folder-open">OPEN <svg class="icon"><use href="#i-arrow"/></svg></span>`;
      $(".folder-name", card).textContent = folder.name;
      const meta = folder.exists ? `${fmtNum(folder.count)} files · ${fmtDisk(folder.bytes)}` : "missing";
      $(".folder-meta", card).textContent = meta;
      $(".folder-path", card).textContent = folder.path;
      folderBox.appendChild(card);
    }
  }

  setText("cmd-boot-count", actions.length);
  const services = data.services || [];
  setText("cmd-service-count", `${services.filter((s) => s.up).length}/${services.length}`);
  const mediaRoot = folders.find((f) => f.id === "media-root");
  setText("cmd-media-size", mediaRoot ? fmtDisk(mediaRoot.bytes) : "—");

  setText("cmd-folder-count", folders.length);
  setText("cmd-updated", `UPDATED ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
  $("#cmd-live-note").innerHTML = '<span class="live-dot"></span>live probes';
}

async function loadCommander() {
  try {
    const data = await api("/api/commander");
    renderCommander(data);
    setNotice("commander-notice", "");
  } catch (error) {
    setNotice("commander-notice", `Commander HUD unavailable: ${error.message}`, true);
    setText("cmd-updated", "FEED ERROR");
  }
}

async function runCommanderAction(actionId, name) {
  if (!actionId) return;
  setNotice("commander-notice", `Opening ${name}…`);
  try {
    const result = await api("/api/commander/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: actionId }),
    });
    toast(result.message || `Opened ${name}.`);
    setNotice("commander-notice", result.message || `Opened ${name}.`);
    setTimeout(loadCommander, 1200);
  } catch (error) {
    setNotice("commander-notice", error.message, true);
    toast(error.message, true);
  }
}

function initCommander() {
  $("#commander-refresh").addEventListener("click", () => {
    loadCommander();
    toast("Commander HUD refreshed.");
  });
  for (const box of [$("#boot-grid"), $("#folder-grid")]) {
    if (!box) continue;
    box.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button) return;
      runCommanderAction(button.dataset.action, button.dataset.name || "action");
    });
  }
}

/* ---------- agent manager ---------- */
let managerProvider = localStorage.getItem("lygoManagerProviderV2") || "nvidia";
if (!["qwen", "nvidia"].includes(managerProvider)) managerProvider = "nvidia";
const agentState = {
  histories: { manager: [], a9lyra: [], lightfather: [] },
  busy: { manager: false, a9lyra: false, lightfather: false },
  status: null,
};

function appendAgentMessage(persona, role, text) {
  const box = document.getElementById(persona === "manager" ? "manager-messages" : `${persona === "a9lyra" ? "a9" : "lf"}-messages`);
  if (!box) return;
  const typing = $(".agent-msg.typing", box);
  if (typing) typing.remove();
  const el = document.createElement("div");
  el.className = `agent-msg ${role}`;
  el.textContent = text;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function agentTyping(persona) {
  const box = document.getElementById(persona === "manager" ? "manager-messages" : `${persona === "a9lyra" ? "a9" : "lf"}-messages`);
  if (!box) return;
  const el = document.createElement("div");
  el.className = "agent-msg assistant typing";
  el.textContent = "thinking through the lattice…";
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function agentProvider(persona, text, cls) {
  const el = document.getElementById(persona === "manager" ? "manager-provider" : `${persona === "a9lyra" ? "a9" : "lf"}-provider`);
  if (!el) return;
  el.textContent = text;
  el.className = `agent-provider ${cls || ""}`;
}

async function sendAgent(persona, text) {
  const message = (text || "").trim();
  if (!message || agentState.busy[persona]) return;
  agentState.busy[persona] = true;
  appendAgentMessage(persona, "user", message);
  agentTyping(persona);
  agentProvider(persona, "thinking…", "");
  const history = agentState.histories[persona].slice(-10);
  try {
    const result = await api("/api/agents/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona, message, history, provider: persona === "manager" ? managerProvider : undefined }),
    });
    agentState.histories[persona].push({ role: "user", content: message });
    agentState.histories[persona].push({ role: "assistant", content: result.reply || "" });
    agentState.histories[persona] = agentState.histories[persona].slice(-20);
    appendAgentMessage(persona, "assistant", result.reply || "(empty reply)");
    if (persona === "manager") {
      agentProvider(persona, result.provider || (managerProvider === "qwen" ? "LYGO Engine" : "NVIDIA free"), "live");
    } else {
      agentProvider(persona, result.provider === "stream-4b" ? "stream 4B" : (result.provider || "stream"), "live");
    }
  } catch (error) {
    appendAgentMessage(persona, "system", `Bridge error: ${error.message}`);
    agentProvider(persona, "bridge error", "error");
  } finally {
    agentState.busy[persona] = false;
    setText("agents-updated", `UPDATED ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
    const input = document.querySelector(`[data-agent-input="${persona}"]`);
    if (input) input.value = "";
  }
}

function renderAgentsStatus(data) {
  agentState.status = data;
  const models = data.models || {};
  for (const [key, id] of [["nvidia", "model-nvidia"], ["qwen", "model-qwen"]]) {
    const el = document.getElementById(id);
    if (!el) continue;
    const online = models[key] && models[key].online;
    el.classList.toggle("up", !!online);
    el.classList.toggle("down", !online);
    el.title = online ? "Bridge online" : (models[key] && models[key].error) || "Bridge offline";
  }
  for (const [key, id] of [["nvidia", "model-nvidia"], ["qwen", "model-qwen"]]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("selected", key === managerProvider);
  }
  const bots = data.bots || {};
  for (const [key, label] of [["a9", "a9"], ["lf", "lf"]]) {
    const bot = bots[key] || {};
    const state = document.getElementById(`${label}-bot-state`);
    const provider = document.getElementById(`${label === "a9" ? "a9" : "lf"}-provider`);
    if (state) {
      state.textContent = bot.online ? `online · PID ${bot.pid || "—"} · task ${bot.task || "—"}` : `offline · task ${bot.task || "—"}`;
      state.className = `bot-state ${bot.online ? "online" : "offline"}`;
    }
    if (provider) {
      provider.textContent = bot.online ? "stream live" : "stream offline";
      provider.className = `agent-provider ${bot.online ? "live" : "error"}`;
    }
  }
  const pred = data.prediction || {};
  const dot = $("#prediction-dot");
  const btn = $("#prediction-restart");
  const warning = $("#prediction-warning");
  if (dot) {
    dot.style.background = pred.online ? "var(--green)" : "var(--red)";
    dot.style.boxShadow = pred.online ? "0 0 8px var(--green)" : "0 0 7px rgba(255,113,136,.6)";
  }
  setText("prediction-state", pred.online ? "Predictions bot online" : "Predictions bot offline");
  setText("prediction-detail", pred.online ? `port ${pred.port || 8791} · PID ${pred.pid || "—"} · task ${pred.task_state || "—"}` : (pred.error || "stream PC unreachable"));
  if (btn) btn.innerHTML = `<svg class="icon"><use href="#i-refresh"/></svg>${pred.online ? "Restart" : "Launch"}`;
  if (warning) {
    const err = String(pred.last_error || "");
    const oauth = /401|oauth|unauthorized/i.test(err);
    warning.hidden = !err;
    warning.textContent = err ? (oauth ? `Prediction bot warning: OAuth 401 — restart will not replace an invalid token. ${err}` : `Prediction bot warning: ${err}`) : "";
  }
  setText("agents-updated", `UPDATED ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`);
}

async function loadAgents() {
  try {
    const data = await api("/api/agents/status");
    renderAgentsStatus(data);
  } catch (error) {
    setNotice("agents-notice", `Agent status unavailable: ${error.message}`, true);
    setText("agents-updated", "FEED ERROR");
  }
}

async function restartPredictions() {
  const btn = $("#prediction-restart");
  if (btn) btn.disabled = true;
  setNotice("agents-notice", "Restarting predictions on the stream PC…");
  try {
    const result = await api("/api/agents/predictions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "restart" }),
    });
    setNotice("agents-notice", result.online ? `Predictions restarted on port ${result.port}.` : "Restart command returned, but predictions are not listening yet.");
    toast(result.online ? "Predictions restarted." : "Predictions did not come back online.", !result.online);
    await loadAgents();
  } catch (error) {
    setNotice("agents-notice", error.message, true);
    toast(error.message, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function sendStreamTask(title, body) {
  if (!body.trim()) { setNotice("stream-task-notice", "Task body required.", true); return; }
  const button = $("#stream-task-send");
  if (button) button.disabled = true;
  setNotice("stream-task-notice", "Queueing on the stream bus…");
  try {
    const result = await api("/api/agents/task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.trim() || "Agent Manager task", body }),
    });
    setNotice("stream-task-notice", result.message || "Task queued.");
    toast("Task queued on the stream PC bus.");
    $("#stream-task-title").value = "";
    $("#stream-task-body").value = "";
  } catch (error) {
    setNotice("stream-task-notice", error.message, true);
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function runBotAction(actionId) {
  const buttons = $$(`[data-bot-action="${actionId}"]`);
  buttons.forEach((button) => { button.disabled = true; });
  setNotice("agents-notice", `${actionId.replace(/_/g, " ")} → stream PC…`);
  try {
    const result = await api("/api/agents/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: actionId }),
    });
    const text = result.message || (result.ok ? "Action completed." : result.error || "Action failed.");
    const persona = actionId.endsWith("_a9") ? "a9lyra" : actionId.endsWith("_lf") ? "lightfather" : "manager";
    appendAgentMessage(persona, "system", `Stream action · ${actionId.replace(/_/g, " ")}: ${text}`);
    setNotice("agents-notice", text);
    toast(text);
    await loadAgents();
  } catch (error) {
    setNotice("agents-notice", error.message, true);
    toast(error.message, true);
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function setManagerProvider(provider) {
  managerProvider = provider === "nvidia" ? "nvidia" : "qwen";
  localStorage.setItem("lygoManagerProviderV2", managerProvider);
  const q = $("#model-qwen");
  const n = $("#model-nvidia");
  if (q) q.classList.toggle("selected", managerProvider === "qwen");
  if (n) n.classList.toggle("selected", managerProvider === "nvidia");
  toast(`Manager model: ${managerProvider === "qwen" ? "LYGO Engine local" : "NVIDIA official"}.`);
}

function initAgents() {
  for (const form of $$("[data-agent-form]")) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const persona = form.dataset.agentForm;
      const input = document.querySelector(`[data-agent-input="${persona}"]`);
      if (input) sendAgent(persona, input.value);
    });
  }
  for (const row of $$("[data-quick]")) {
    row.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      const persona = row.dataset.quick;
      sendAgent(persona, button.textContent.trim());
    });
  }
  $("#agents-refresh").addEventListener("click", () => { loadAgents(); loadKeys(); toast("Agent status refreshed."); });
  $("#model-qwen").addEventListener("click", () => setManagerProvider("qwen"));
  $("#model-nvidia").addEventListener("click", () => setManagerProvider("nvidia"));
  for (const button of $$("[data-bot-action]")) {
    button.addEventListener("click", () => runBotAction(button.dataset.botAction));
  }
  $("#prediction-restart").addEventListener("click", restartPredictions);
  $("#stream-task-form").addEventListener("submit", (event) => {
    event.preventDefault();
    sendStreamTask($("#stream-task-title").value, $("#stream-task-body").value);
  });
}

/* ---------- full console ---------- */
async function loadConsole() {
  const frame = $("#console-frame");
  const down = $("#console-down");
  const state = $("#console-state");
  try {
    const data = await api("/api/console/status");
    if (data.online) {
      if (frame && frame.dataset.loaded !== "1") {
        frame.src = data.url;
        frame.dataset.loaded = "1";
      }
      if (down) down.hidden = true;
      if (state) { state.textContent = "console live"; state.className = "mini-status ok"; }
    } else {
      if (down) down.hidden = false;
      if (state) { state.textContent = "console offline"; state.className = "mini-status bad"; }
    }
  } catch (error) {
    if (down) down.hidden = false;
    if (state) { state.textContent = "console offline"; state.className = "mini-status bad"; }
    setNotice("console-notice", error.message, true);
  }
}

async function bootConsole() {
  const button = $("#console-boot");
  if (button) button.disabled = true;
  setNotice("console-notice", "Booting LYGO Console 1.9 on port 9641…");
  try {
    const result = await api("/api/console/start", { method: "POST" });
    if (!result.online) throw new Error(result.error || "console did not answer");
    $("#console-frame").dataset.loaded = "";
    /* The console is the host window now; do not load a second console iframe here. */
    setNotice("console-notice", "Console is live.");
    toast("LYGO Console 1.9 is live.");
  } catch (error) {
    setNotice("console-notice", error.message, true);
    toast(error.message, true);
  } finally {
    if (button) button.disabled = false;
  }
}

function initConsole() {
  const boot = $("#console-boot");
  if (boot) boot.addEventListener("click", bootConsole);
}

function initDock() {
  $("#activity-btn").addEventListener("click", () => {
    const job = state.jobs[0] || state.activeJob;
    if (job) openJob(job);
    else toast("No jobs in this session yet.");
  });
  $("#close-job").addEventListener("click", () => {
    $("#job-dock").hidden = true;
    state.activeJob = null;
  });
  $("#copy-log").addEventListener("click", async () => {
    const text = $("#job-log").textContent || "";
    try {
      await navigator.clipboard.writeText(text);
      toast("Job log copied.");
    } catch {
      toast("Clipboard permission was denied.", true);
    }
  });
  $("#clear-jobs").addEventListener("click", clearFinishedJobs);
  $("#genesis-refresh").addEventListener("click", () => {
    loadGenesis();
    toast("Genesis HUD refreshed.");
  });
}

function initShortcuts() {
  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if (event.key === "Escape" && !$("#job-dock").hidden) {
      $("#job-dock").hidden = true;
      state.activeJob = null;
    }
    if (["1", "2", "3", "4", "5", "6", "7"].includes(event.key)) {
      selectTab({ 1: "command", 2: "agents", 3: "agents", 4: "signal", 5: "music", 6: "photos", 7: "genesis" }[event.key]);
    }
  });
}

async function init() {
  if (window.self !== window.top) {
    const consoleTab = document.querySelector('[data-tab="console"]');
    const consolePanel = document.getElementById('panel-console');
    if (consoleTab) consoleTab.hidden = true;
    if (consolePanel) consolePanel.hidden = true;
  }
  initTabs();
  initSignal();
  initMusic();
  initMusicExcav();
  initPhotos();
  initDock();
  initCommander();
  initConsole();
  initAgents();
  initShortcuts();
  await loadConfig();
  await loadOverview();
  await loadJobs();
  await loadCommander();
  await loadConsole();
  await loadAgents();
  await loadGenesis();
  setInterval(loadOverview, 30000);
  setInterval(loadJobs, 15000);
  setInterval(loadCommander, 15000);
  setInterval(loadAgents, 20000);
  setInterval(loadGenesis, 15000);
}

init();

async function loadKeys() {
  const box = document.querySelector("#keys-strip .keys-body");
  if (!box) return;
  try {
    const d = await (await fetch("/api/keys", { cache: "no-store" })).json();
    const parts = (d.providers || []).map(p => {
      const dot = p.ok ? "🟢" : (p.configured ? "🔴" : "⚪");
      const ms = p.ok ? ` ${p.ms}ms` : (p.status ? ` ${p.status}` : "");
      const tag = p.active ? "*" : "";
      return `<span class="key-chip" title="${p.detail || ''}">${dot} ${p.provider}${tag}${ms}</span>`;
    });
    const e = d.engine || {};
    const eng = e.brain
      ? ` · engine ${e.brain}${e.selected ? " · " + e.selected : ""}`
      : "";
    box.innerHTML = parts.join(" ") + `<span class="key-chip">${eng}</span>`;
  } catch (err) {
    box.textContent = "keys unavailable";
  }
}
