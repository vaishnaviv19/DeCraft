// DeCraft Studio: upload PDFs or a folder, follow each run, preview and download the decoys.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const state = { jobs: [], info: null, viewer: null, polling: null };

// ---------------------------------------------------------------- helpers
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c.nodeType ? c : document.createTextNode(c));
  return node;
}
function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}
const fmtTime = s => s == null ? "" : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
const newBatchId = () => Math.random().toString(36).slice(2, 10);

// ---------------------------------------------------------------- model status
async function loadInfo() {
  const info = await (await fetch("/api/info")).json();
  state.info = info;
  const box = $("#modelStatus");
  box.replaceChildren(
    el("span", { class: "pill" + (info.keys.text ? " on" : "") }, el("span", { class: "dot" }), info.keys.text ? "Text model ready" : "No text model key"),
    el("span", { class: "pill" + (info.keys.vision ? " on" : "") }, el("span", { class: "dot" }), info.keys.vision ? "Vision model ready" : "No vision model key"),
  );
  if (!info.keys.text && !info.keys.vision) {
    $("#modelsToggle").classList.add("disabled");
    $("#useModels").disabled = true;
    $("#modelsToggle").title = "Set GROQ_API_KEY and/or CF_ACCOUNT_ID + CF_API_TOKEN before starting the studio";
  }
}

// ---------------------------------------------------------------- collecting files
function walkEntry(entry, path = "") {
  return new Promise(resolve => {
    if (entry.isFile) {
      entry.file(file => resolve([{ file, path: path + file.name }]), () => resolve([]));
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const all = [];
      const read = () => reader.readEntries(async entries => {
        if (!entries.length) {
          const nested = await Promise.all(all.map(e => walkEntry(e, path + entry.name + "/")));
          resolve(nested.flat());
        } else { all.push(...entries); read(); }
      }, () => resolve([]));
      read();
    } else resolve([]);
  });
}

async function filesFromDrop(event) {
  const items = [...(event.dataTransfer.items || [])];
  const entries = items.map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (entries.length) return (await Promise.all(entries.map(e => walkEntry(e)))).flat();
  return [...event.dataTransfer.files].map(file => ({ file, path: file.name }));
}

function onlyPdfs(list) {
  const pdfs = list.filter(({ file }) => /\.pdf$/i.test(file.name));
  const skipped = list.length - pdfs.length;
  if (skipped) toast(`${skipped} non-PDF file${skipped > 1 ? "s" : ""} skipped`);
  return pdfs;
}

// ---------------------------------------------------------------- upload
async function upload(list) {
  const pdfs = onlyPdfs(list);
  if (!pdfs.length) { toast("No PDF files found"); return; }
  const batch = newBatchId();
  const useModels = $("#useModels").checked, fresh = $("#fresh").checked;
  let sent = 0;
  for (const { file, path } of pdfs) {
    toast(`Uploading ${++sent} of ${pdfs.length}: ${file.name}`);
    try {
      const response = await fetch("/api/upload", {
        method: "POST", body: file,
        headers: {
          "X-Filename": encodeURIComponent(file.name), "X-Relative-Path": encodeURIComponent(path),
          "X-Batch": batch, "X-Use-Models": useModels ? "1" : "0", "X-Fresh": fresh ? "1" : "0",
        },
      });
      if (!response.ok) toast((await response.json()).error || `Upload of ${file.name} failed`);
    } catch { toast(`Upload of ${file.name} failed`); }
    await refresh();
  }
  toast(`${pdfs.length} PDF${pdfs.length > 1 ? "s" : ""} queued`);
}

// ---------------------------------------------------------------- rendering
const docIcon = () => {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.innerHTML = '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>';
  return svg;
};

function jobCard(job) {
  const p = job.progress, pct = Math.round(100 * p.done / p.total);
  const sum = job.summary;
  const review = job.status === "done" && sum && sum.gates_passed < sum.gates_total;
  const chipText = { queued: "Queued", running: "Generating", done: review ? "Needs review" : "Ready", failed: "Failed" }[job.status];
  const chipClass = review ? "review" : job.status;
  const thumb = job.has_pdf && job.pages ? el("img", { src: `/api/jobs/${job.id}/page/1?side=decoy&t=${job.finished || ""}`, alt: "" }) : docIcon();

  const meta = [];
  if (job.status === "running" || job.status === "queued") meta.push(el("span", {}, `${p.label}${job.status === "running" ? ` · step ${Math.min(p.done + 1, p.total)} of ${p.total}` : ""}`));
  if (job.elapsed != null) meta.push(el("span", {}, fmtTime(job.elapsed)));
  if (sum) meta.push(el("span", {}, `${sum.gates_passed}/${sum.gates_total} quality checks passed`));
  if (job.pages) meta.push(el("span", {}, `${job.pages} pages`));
  if (job.llm) meta.push(el("span", {}, "AI models on"));
  if (job.status === "failed" && job.message) meta.push(el("span", { class: "msg" }, job.message));

  const actions = [];
  if (job.has_pdf) {
    actions.push(el("button", { class: "btn small", onclick: () => openViewer(job) }, "Preview"));
    actions.push(el("a", { class: "btn small", href: `/api/jobs/${job.id}/package` }, "Full package"));
    actions.push(el("a", { class: "btn small primary", href: `/api/jobs/${job.id}/pdf` }, "Download PDF"));
  } else if (job.status === "failed") {
    actions.push(el("a", { class: "btn small", href: `/api/jobs/${job.id}/log`, target: "_blank" }, "View log"));
  }

  return el("article", { class: `job ${job.status}`, "data-id": job.id },
    el("div", { class: "job-icon" }, thumb),
    el("div", { class: "job-main" },
      el("div", { class: "job-title" }, el("b", { title: job.name }, job.name), el("span", { class: `chip ${chipClass}` }, chipText)),
      job.folder ? el("div", { class: "job-folder" }, job.folder + "/") : null,
      job.status !== "done" ? el("div", { class: "progress", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 }, el("span", { style: `width:${job.status === "failed" ? 100 : Math.max(pct, job.status === "running" ? 4 : 0)}%` })) : null,
      el("div", { class: "job-meta" }, meta),
    ),
    el("div", { class: "job-actions" }, actions),
  );
}

function render() {
  const groups = new Map();
  for (const job of state.jobs) {
    if (!groups.has(job.batch)) groups.set(job.batch, []);
    groups.get(job.batch).push(job);
  }
  const ordered = [...groups.entries()].sort((a, b) => Math.max(...b[1].map(j => j.created)) - Math.max(...a[1].map(j => j.created)));
  const container = $("#batches");
  container.replaceChildren(...ordered.map(([batch, jobs], index) => {
    const n = s => jobs.filter(j => j.status === s).length;
    const done = n("done"), running = n("running"), queued = n("queued"), failed = n("failed");
    const counts = [done && `${done} ready`, running && `${running} generating`, queued && `${queued} waiting`, failed && `${failed} failed`].filter(Boolean).join(" · ");
    const when = new Date(Math.min(...jobs.map(j => j.created)) * 1000);
    return el("section", { class: "batch card" },
      el("div", { class: "batch-head" },
        el("div", {},
          el("h3", {}, index === 0 ? "Latest upload" : `Upload of ${when.toLocaleDateString()} ${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`),
          el("div", { class: "counts" }, `${jobs.length} document${jobs.length > 1 ? "s" : ""}${counts ? " · " + counts : ""}`)),
        el("a", { class: "btn primary" + (done ? "" : " disabled"), href: `/api/batches/${batch}/zip`, "aria-disabled": done ? "false" : "true" }, "Download all (.zip)")),
      el("div", { class: "jobs" }, jobs.map(jobCard)));
  }));
  $("#empty").hidden = state.jobs.length > 0;
}

async function refresh() {
  try {
    state.jobs = await (await fetch("/api/jobs")).json();
    render();
  } catch { /* server restarting */ }
  const active = state.jobs.some(j => j.status === "queued" || j.status === "running");
  clearTimeout(state.polling);
  state.polling = setTimeout(refresh, active ? 1500 : 6000);
}

// ---------------------------------------------------------------- viewer
function openViewer(job) {
  state.viewer = { job, page: 1 };
  $("#viewerTitle").textContent = job.name;
  $("#viewerSub").textContent = job.summary ? `${job.pages} pages · ${job.summary.gates_passed}/${job.summary.gates_total} quality checks passed` : `${job.pages} pages`;
  $("#viewer").hidden = false;
  showPage(1);
  $("#viewerClose").focus();
}
function showPage(n) {
  const v = state.viewer;
  if (!v) return;
  const total = v.job.pages;
  v.page = Math.min(Math.max(1, n), total);
  $("#imgDecoy").src = `/api/jobs/${v.job.id}/page/${v.page}?side=decoy`;
  const source = $("#imgSource");
  source.src = `/api/jobs/${v.job.id}/page/${v.page}?side=source`;
  source.onerror = () => { source.removeAttribute("src"); source.alt = "Added chart page (no original)"; };
  $("#pager").replaceChildren(...Array.from({ length: total }, (_, i) =>
    el("button", { class: i + 1 === v.page ? "active" : "", onclick: () => showPage(i + 1), "aria-label": `Page ${i + 1}` }, String(i + 1))));
  $("#prevPage").disabled = v.page === 1;
  $("#nextPage").disabled = v.page === total;
}
function closeViewer() { $("#viewer").hidden = true; state.viewer = null; }

// ---------------------------------------------------------------- wiring
function wire() {
  const zone = $("#dropzone");
  ["dragenter", "dragover"].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); zone.classList.add("dragging"); }));
  ["dragleave", "drop"].forEach(t => zone.addEventListener(t, e => { e.preventDefault(); if (t === "drop" || e.target === zone) zone.classList.remove("dragging"); }));
  zone.addEventListener("drop", async e => upload(await filesFromDrop(e)));
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", e => e.preventDefault());

  $("#folderInput").addEventListener("change", e => { upload([...e.target.files].map(file => ({ file, path: file.webkitRelativePath || file.name }))); e.target.value = ""; });
  $("#fileInput").addEventListener("change", e => { upload([...e.target.files].map(file => ({ file, path: file.name }))); e.target.value = ""; });

  $("#viewerClose").addEventListener("click", closeViewer);
  $("#viewer").addEventListener("click", e => { if (e.target.id === "viewer") closeViewer(); });
  $("#prevPage").addEventListener("click", () => showPage(state.viewer.page - 1));
  $("#nextPage").addEventListener("click", () => showPage(state.viewer.page + 1));
  document.querySelectorAll(".seg button").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".seg button").forEach(x => x.classList.toggle("active", x === b));
    $("#viewerBody").classList.toggle("decoy-only", b.dataset.mode === "decoy");
  }));
  document.addEventListener("keydown", e => {
    if (!state.viewer) return;
    if (e.key === "Escape") closeViewer();
    if (e.key === "ArrowLeft") showPage(state.viewer.page - 1);
    if (e.key === "ArrowRight") showPage(state.viewer.page + 1);
  });
}

wire();
loadInfo();
refresh();
