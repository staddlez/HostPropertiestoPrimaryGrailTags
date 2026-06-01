/* ─── Utilities ──────────────────────────────────────────────── */
const $ = (sel) => document.querySelector(sel);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[ch]));
}

function showLoading(label = "Working…") {
  $("#loadingLabel").textContent = label;
  $("#loadingOverlay").classList.remove("hidden");
  setFormBusy(true);
}
function hideLoading() {
  $("#loadingOverlay").classList.add("hidden");
  setFormBusy(false);
}
function setFormBusy(busy) {
  ["buildPayload", "validateRemoteConfig", "previewRemoteConfig", "createJob",
   "getCurrentJob", "listJobs", "getJobById", "tagPreview", "tagRun"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.disabled = busy;
  });
}

/* ─── Config ─────────────────────────────────────────────────── */
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    $("#dtEnv").textContent = cfg.dt_env || "Not configured";
    $("#tokenMasked").textContent = cfg.token_masked || "—";
    const statusEl = $("#configStatus");
    if (cfg.configured) {
      statusEl.textContent = "Ready";
      statusEl.className = "badge ok";
    } else {
      statusEl.textContent = "Not configured";
      statusEl.className = "badge err";
    }
  } catch {
    $("#configStatus").textContent = "Error";
    $("#configStatus").className = "badge err";
  }
}

/* ─── Sidebar navigation ─────────────────────────────────────── */
document.querySelectorAll(".navitem").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".navitem").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    const target = document.getElementById(btn.dataset.tab);
    if (target) target.classList.add("active");
  });
});

/* ─── File input UX ──────────────────────────────────────────── */
const fileInput = $("#remoteConfigFile");
const fileDrop = $("#fileDrop");
const fileLabel = $("#fileLabel");

fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (file) {
    fileLabel.textContent = file.name;
    fileDrop.classList.add("has-file");
  } else {
    fileLabel.textContent = "Drop CSV or click to browse";
    fileDrop.classList.remove("has-file");
  }
});

/* ─── Form data ──────────────────────────────────────────────── */
function buildFormData() {
  const file = fileInput.files[0];
  if (!file) throw new Error("Choose a CSV file first.");
  const fd = new FormData();
  fd.append("file", file);
  fd.append("restart", $("#restart").checked ? "true" : "false");
  fd.append("operation", $("#operationMode").value || "set");
  fd.append("lowercaseKeyValues", $("#lowercaseKeyValues").checked ? "true" : "false");
  fd.append("lowercaseExceptionKeys", $("#lowercaseExceptionKeys").value || "");
  fd.append("whitespaceToUnderscore", $("#whitespaceToUnderscore").checked ? "true" : "false");
  return fd;
}

/* ─── Summary bar ────────────────────────────────────────────── */
function renderSummary(data) {
  const bar = $("#summaryBar");
  bar.classList.remove("hidden");
  const s = data.summary;
  if (s) {
    bar.innerHTML = `
      <span class="stat-pill"><span>Rows</span><strong>${s.rows}</strong></span>
      <span class="stat-pill"><span>Operations</span><strong>${s.operations}</strong></span>
      <span class="stat-pill good"><span>Success</span><strong>${s.successfulRows}</strong></span>
      <span class="stat-pill bad"><span>Failed</span><strong>${s.failedRows}</strong></span>
      ${s.totalWarnings ? `<span class="stat-pill warn"><span>Warnings</span><strong>${s.totalWarnings}</strong></span>` : ""}
    `;
  } else {
    bar.innerHTML = `
      <span class="stat-pill"><span>Rows</span><strong>${data.row_count ?? 0}</strong></span>
      <span class="stat-pill"><span>Operations</span><strong>${data.operation_count ?? 0}</strong></span>
      ${data.errors?.length ? `<span class="stat-pill bad"><span>Errors</span><strong>${data.errors.length}</strong></span>` : ""}
      ${data.warnings?.length ? `<span class="stat-pill warn"><span>Warnings</span><strong>${data.warnings.length}</strong></span>` : ""}
    `;
  }
}

/* ─── Warning / error banners ────────────────────────────────── */
function renderBanner(selector, items, titleText) {
  const el = $(selector);
  if (!items || !items.length) {
    el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = `
    <div class="banner-title">${titleText} (${items.length})</div>
    <ul class="banner-list">${items.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>
  `;
}

/* ─── Review table ───────────────────────────────────────────── */
function renderReview(rows) {
  const body = $("#reviewBody");
  if (!rows || !rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty-cell">No rows to review.</td></tr>`;
    return;
  }
  body.innerHTML = "";
  rows.forEach((r) => {
    const changes = r.changes || [];
    if (!changes.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${r.row ?? ""}</td>
        <td>${escapeHtml(r.entityName)}</td>
        <td><code>${escapeHtml(r.entityID)}</code></td>
        <td colspan="3" class="empty-cell" style="text-align:left">No attribute columns.</td>
      `;
      body.appendChild(tr);
      return;
    }
    changes.forEach((c, idx) => {
      const tr = document.createElement("tr");
      if (c.changed) tr.classList.add("changed-row");
      const finalDisplay = c.changed
        ? `<span class="changed-val">${escapeHtml(c.finalCell)}</span>`
        : escapeHtml(c.finalCell);
      tr.innerHTML = `
        <td>${idx === 0 ? (r.row ?? "") : ""}</td>
        <td>${idx === 0 ? escapeHtml(r.entityName) : ""}</td>
        <td>${idx === 0 ? `<code>${escapeHtml(r.entityID)}</code>` : ""}</td>
        <td><code>${escapeHtml(c.attribute)}</code></td>
        <td>${escapeHtml(c.originalCell)}</td>
        <td>${finalDisplay}</td>
      `;
      body.appendChild(tr);
    });
  });
}

/* ─── Payload JSON preview ───────────────────────────────────── */
function renderPayloadReview(rows) {
  const el = $("#payloadReviewJson");
  const payloads = (rows || []).map((r) => ({ row: r.row, entityName: r.entityName, payload: r.payload }));
  el.textContent = JSON.stringify(payloads, null, 2);
}

/* ─── API results table ──────────────────────────────────────── */
function renderRows(rows, mode) {
  const body = $("#resultsBody");
  if (!rows || !rows.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty-cell">No results.</td></tr>`;
    return;
  }
  body.innerHTML = "";
  rows.forEach((r) => {
    // For build mode, operations come from r.operations.
    // For API results mode, they come from r.payload.operations.
    const operations = r.operations || r.payload?.operations || [];
    const isBuild = mode === "build";
    let statusHtml;
    if (isBuild) {
      statusHtml = `<span class="badge-neutral">Built locally</span>`;
    } else if (r.success) {
      statusHtml = `<span class="badge-good">✓ ${r.statusCode}</span>`;
    } else {
      statusHtml = `<span class="badge-bad">✗ ${r.statusCode ?? "Error"}</span>`;
    }
    const opsHtml = operations
      .map((op) => `<code>${escapeHtml(op.attribute)} ${escapeHtml(op.operation)}${op.value ? "=" + escapeHtml(op.value) : ""}</code>`)
      .join(" ");
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.row ?? ""}</td>
      <td>${escapeHtml(r.entityName ?? "")}</td>
      <td><code>${escapeHtml(r.entityID ?? "")}</code></td>
      <td style="font-size:11px;line-height:1.8">${opsHtml}</td>
      <td>${statusHtml}</td>
    `;
    body.appendChild(tr);
  });
}

/* ─── Core post helper ───────────────────────────────────────── */
async function postCsv(endpoint, mode, loadingText) {
  let fd;
  try {
    fd = buildFormData();
  } catch (err) {
    alert(err.message);
    return;
  }
  showLoading(loadingText);
  try {
    const res = await fetch(endpoint, { method: "POST", body: fd });
    const data = await res.json();

    // Collect all warnings across build and API responses
    const allWarnings = [
      ...(data.warnings || []),
      ...((data.rows || []).flatMap((r) => r.warnings || [])),
      ...((data.results || []).flatMap((r) => r.warnings || [])),
    ];

    const rows = data.results || data.rows || [];
    renderRows(rows, mode);
    renderReview(rows);
    renderPayloadReview(rows);
    renderSummary(data);
    renderBanner("#warningsBanner", allWarnings, "⚠ Warnings");
    renderBanner("#errorsBanner", data.errors, "✖ Errors");
    $("#rawJson").textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    $("#rawJson").textContent = JSON.stringify({ ok: false, error: err.message }, null, 2);
    renderBanner("#errorsBanner", [err.message], "✖ Error");
  } finally {
    hideLoading();
  }
}

/* ─── Button wiring ──────────────────────────────────────────── */
$("#buildPayload").addEventListener("click", () =>
  postCsv("/api/remote-config/build-payload-csv", "build", "Parsing CSV…")
);
$("#validateRemoteConfig").addEventListener("click", () =>
  postCsv("/api/remote-config/validate-csv", "api", "Validating with Dynatrace…")
);
$("#previewRemoteConfig").addEventListener("click", () =>
  postCsv("/api/remote-config/preview-csv", "api", "Running Dynatrace preview…")
);
$("#remoteConfigForm").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!confirm("Create the remote config job in Dynatrace? This will apply to all entities in the CSV.")) return;
  postCsv("/api/remote-config/run-csv", "api", "Creating job…");
});

$("#clearResults").addEventListener("click", () => {
  $("#reviewBody").innerHTML = `<tr><td colspan="6" class="empty-cell">Run "Build &amp; review" to inspect changes before sending to Dynatrace.</td></tr>`;
  $("#resultsBody").innerHTML = `<tr><td colspan="5" class="empty-cell">No API results yet.</td></tr>`;
  $("#payloadReviewJson").textContent = "[]";
  $("#rawJson").textContent = "{}";
  $("#summaryBar").classList.add("hidden");
  $("#summaryBar").innerHTML = "";
  $("#warningsBanner").classList.add("hidden");
  $("#errorsBanner").classList.add("hidden");
});

/* ─── Job status ─────────────────────────────────────────────── */
async function jobFetch(url, label) {
  showLoading(label);
  try {
    const res = await fetch(url);
    const data = await res.json();
    $("#jobRawJson").textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    $("#jobRawJson").textContent = JSON.stringify({ ok: false, error: err.message }, null, 2);
  } finally {
    hideLoading();
  }
}

$("#getCurrentJob").addEventListener("click", () =>
  jobFetch("/api/remote-config/current", "Fetching current job…")
);
$("#listJobs").addEventListener("click", () => {
  const qs = new URLSearchParams();
  const from = $("#jobsFrom").value.trim();
  const to = $("#jobsTo").value.trim();
  if (from) qs.set("from", from);
  if (to) qs.set("to", to);
  jobFetch(`/api/remote-config/jobs?${qs.toString()}`, "Fetching jobs…");
});
$("#getJobById").addEventListener("click", () => {
  const id = $("#jobId").value.trim();
  if (!id) { alert("Enter a job ID first."); return; }
  jobFetch(`/api/remote-config/jobs/${encodeURIComponent(id)}`, "Fetching job…");
});
$("#clearJobResults").addEventListener("click", () => {
  $("#jobRawJson").textContent = "{}";
});

/* ─── Tag cleanup ────────────────────────────────────────────── */
const tagFileInput = $("#tagFile");
const tagFileDrop = $("#tagFileDrop");
const tagFileLabel = $("#tagFileLabel");

tagFileInput.addEventListener("change", () => {
  const file = tagFileInput.files[0];
  if (file) {
    tagFileLabel.textContent = file.name;
    tagFileDrop.classList.add("has-file");
  } else {
    tagFileLabel.textContent = "Drop CSV or click to browse";
    tagFileDrop.classList.remove("has-file");
  }
});

function renderTagSummary(data) {
  const bar = $("#tagSummaryBar");
  bar.classList.remove("hidden");
  const s = data.summary;
  if (s) {
    // from run response
    bar.innerHTML = `
      <span class="stat-pill"><span>Rows</span><strong>${s.rows}</strong></span>
      <span class="stat-pill good"><span>Success</span><strong>${s.successfulRows}</strong></span>
      <span class="stat-pill bad"><span>Failed</span><strong>${s.failedRows}</strong></span>
      <span class="stat-pill"><span>DELETEs</span><strong>${s.totalDeleteOps}</strong></span>
      <span class="stat-pill"><span>POSTs</span><strong>${s.totalAddOps}</strong></span>
    `;
  } else {
    // from preview response
    bar.innerHTML = `
      <span class="stat-pill"><span>Rows</span><strong>${data.row_count ?? 0}</strong></span>
      <span class="stat-pill bad"><span>To delete</span><strong>${data.delete_count ?? 0}</strong></span>
      <span class="stat-pill good"><span>To add</span><strong>${data.add_count ?? 0}</strong></span>
      ${data.errors?.length ? `<span class="stat-pill bad"><span>Errors</span><strong>${data.errors.length}</strong></span>` : ""}
    `;
  }
}

function renderTagResults(data, isPreview) {
  const body = $("#tagResultsBody");
  body.innerHTML = "";

  const rows = data.results || data.rows || [];
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty-cell">No rows.</td></tr>`;
    return;
  }

  if (isPreview) {
    // Preview: show what will be sent
    rows.forEach((r) => {
      const allOps = [
        ...r.deleteTags.map((t) => ({ op: "DELETE", tag: t })),
        ...r.addTags.map((t) => ({ op: "POST", tag: t })),
      ];
      if (!allOps.length) return;

      allOps.forEach((item, idx) => {
        const tagStr = item.tag.value
          ? `${escapeHtml(item.tag.key)}=${escapeHtml(item.tag.value)}`
          : escapeHtml(item.tag.key);
        const opBadge = item.op === "DELETE"
          ? `<span style="color:var(--err);font-weight:700;font-size:11px">DELETE</span>`
          : `<span style="color:var(--accent);font-weight:700;font-size:11px">POST</span>`;
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${idx === 0 ? (r.row ?? "") : ""}</td>
          <td>${idx === 0 ? escapeHtml(r.hostname ?? "") : ""}</td>
          <td>${idx === 0 ? `<code>${escapeHtml(r.entityID ?? "")}</code>` : ""}</td>
          <td>${opBadge}</td>
          <td><code>${tagStr}</code></td>
          <td><span class="badge-neutral">Preview</span></td>
        `;
        body.appendChild(tr);
      });
    });
  } else {
    // Run: show per-operation results
    rows.forEach((r) => {
      const ops = r.operations || [];
      ops.forEach((op, idx) => {
        const isDelete = op.operation === "DELETE";
        const opBadge = isDelete
          ? `<span style="color:var(--err);font-weight:700;font-size:11px">DELETE</span>`
          : `<span style="color:var(--accent);font-weight:700;font-size:11px">POST</span>`;

        let tagsHtml;
        if (isDelete) {
          const t = op.tag;
          const tagStr = t.value ? `${escapeHtml(t.key)}=${escapeHtml(t.value)}` : escapeHtml(t.key);
          tagsHtml = `<code>${tagStr}</code>`;
        } else {
          tagsHtml = (op.tags || [])
            .map((t) => `<code>${t.value ? escapeHtml(t.key) + "=" + escapeHtml(t.value) : escapeHtml(t.key)}</code>`)
            .join(" ");
        }

        const statusHtml = op.success
          ? `<span class="badge-good">✓ ${op.statusCode}</span>`
          : `<span class="badge-bad">✗ ${op.statusCode ?? "Error"}</span>`;

        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${idx === 0 ? (r.row ?? "") : ""}</td>
          <td>${idx === 0 ? escapeHtml(r.hostname ?? "") : ""}</td>
          <td>${idx === 0 ? `<code>${escapeHtml(r.entityID ?? "")}</code>` : ""}</td>
          <td>${opBadge}</td>
          <td style="font-size:11px;line-height:1.8">${tagsHtml}</td>
          <td>${statusHtml}</td>
        `;
        body.appendChild(tr);
      });
    });
  }
}

async function runTagOp(endpoint, isPreview, loadingText) {
  const file = tagFileInput.files[0];
  if (!file) { alert("Choose a CSV file first."); return; }

  const fd = new FormData();
  fd.append("file", file);
  fd.append("deleteAllWithKey", $("#deleteAllWithKey").checked ? "true" : "false");

  showLoading(loadingText);
  try {
    const res = await fetch(endpoint, { method: "POST", body: fd });
    const data = await res.json();
    renderTagResults(data, isPreview);
    renderTagSummary(data);
    renderBanner("#tagErrorsBanner", data.errors, "✖ Errors");
    $("#tagRawJson").textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    renderBanner("#tagErrorsBanner", [err.message], "✖ Error");
    $("#tagRawJson").textContent = JSON.stringify({ ok: false, error: err.message }, null, 2);
  } finally {
    hideLoading();
  }
}

$("#tagPreview").addEventListener("click", () =>
  runTagOp("/api/tags/preview-csv", true, "Parsing CSV…")
);

$("#tagRun").addEventListener("click", () => {
  if (!confirm("Run tag operations against Dynatrace? DELETE operations cannot be undone.")) return;
  runTagOp("/api/tags/run-csv", false, "Running tag operations…");
});

$("#clearTagResults").addEventListener("click", () => {
  $("#tagResultsBody").innerHTML = `<tr><td colspan="6" class="empty-cell">Preview or run operations to see results.</td></tr>`;
  $("#tagRawJson").textContent = "{}";
  $("#tagSummaryBar").classList.add("hidden");
  $("#tagSummaryBar").innerHTML = "";
  $("#tagErrorsBanner").classList.add("hidden");
});

/* ─── Init ───────────────────────────────────────────────────── */
loadConfig();
