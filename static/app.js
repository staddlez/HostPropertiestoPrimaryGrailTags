const $ = (sel) => document.querySelector(sel);

async function loadConfig() {
  const res = await fetch("/api/config");
  const cfg = await res.json();
  $("#dtEnv").textContent = cfg.dt_env || "Not configured";
  $("#apiBase").textContent = cfg.api_base || "Not configured";
  $("#tokenMasked").textContent = cfg.token_masked || "Not configured";
}

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("#" + btn.dataset.tab).classList.add("active");
  });
});

function formDataForRemoteConfig() {
  const file = $("#remoteConfigFile").files[0];
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

function renderRows(rows, mode = "preview") {
  const body = $("#resultsBody");
  body.innerHTML = "";
  if (!rows || !rows.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">No rows.</td></tr>`;
    return;
  }

  rows.forEach(r => {
    const operations = r.operations || r.payload?.operations || [];
    const status = mode === "build"
      ? "Built locally"
      : (r.success ? `Success ${r.statusCode}` : `Failed ${r.statusCode ?? ""}`);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.row ?? ""}</td>
      <td>${escapeHtml(r.entityName ?? "")}</td>
      <td><code>${escapeHtml(r.entityID ?? "")}</code></td>
      <td>${operations.map(op => `<code>${escapeHtml(op.attribute)} ${escapeHtml(op.operation || "set")}${op.value ? ":" + escapeHtml(op.value) : ""}</code>`).join(" ")}</td>
      <td><span class="${r.success === false ? "bad" : "good"}">${status}</span></td>
    `;
    body.appendChild(tr);
  });
}


function renderReview(rows) {
  const body = $("#reviewBody");
  if (!body) return;
  body.innerHTML = "";
  if (!rows || !rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty">Build payload first to review final CSV changes.</td></tr>`;
    return;
  }

  rows.forEach(r => {
    const changes = r.changes || [];
    if (!changes.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${r.row ?? ""}</td>
        <td>${escapeHtml(r.entityName ?? "")}</td>
        <td><code>${escapeHtml(r.entityID ?? "")}</code></td>
        <td colspan="3" class="empty">No dynamic attribute columns found.</td>
      `;
      body.appendChild(tr);
      return;
    }

    changes.forEach((c, idx) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${idx === 0 ? (r.row ?? "") : ""}</td>
        <td>${idx === 0 ? escapeHtml(r.entityName ?? "") : ""}</td>
        <td>${idx === 0 ? `<code>${escapeHtml(r.entityID ?? "")}</code>` : ""}</td>
        <td><code>${escapeHtml(c.attribute ?? "")}</code></td>
        <td>${escapeHtml(c.originalCell ?? "")}</td>
        <td>${c.changed ? `<strong>${escapeHtml(c.finalCell ?? "")}</strong>` : escapeHtml(c.finalCell ?? "")}</td>
      `;
      body.appendChild(tr);
    });
  });
}

function renderPayloadReview(rows) {
  const payloadEl = $("#payloadReviewJson");
  if (!payloadEl) return;
  const payloads = (rows || []).map(r => ({
    row: r.row,
    entityName: r.entityName,
    payload: r.payload
  }));
  payloadEl.textContent = JSON.stringify(payloads, null, 2);
}

function renderSummary(data) {
  if (data.summary) {
    $("#summary").innerHTML = `
      <span>Rows: <strong>${data.summary.rows}</strong></span>
      <span>Operations: <strong>${data.summary.operations}</strong></span>
      <span>Success: <strong>${data.summary.successfulRows}</strong></span>
      <span>Failed: <strong>${data.summary.failedRows}</strong></span>
    `;
  } else {
    $("#summary").innerHTML = `
      <span>Rows: <strong>${data.row_count ?? 0}</strong></span>
      <span>Operations: <strong>${data.operation_count ?? 0}</strong></span>
      <span>Errors: <strong>${data.errors?.length ?? 0}</strong></span>
    `;
  }
}

function renderRaw(data) {
  $("#rawJson").textContent = JSON.stringify(data, null, 2);
}

function renderJobRaw(data) {
  $("#jobRawJson").textContent = JSON.stringify(data, null, 2);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  }[ch]));
}

async function postCsv(endpoint, mode) {
  try {
    const res = await fetch(endpoint, { method: "POST", body: formDataForRemoteConfig() });
    const data = await res.json();
    const rows = data.results || data.rows || [];
    renderRows(rows, mode);
    renderReview(rows);
    renderPayloadReview(rows);
    renderSummary(data);
    renderRaw(data);
  } catch (err) {
    renderRaw({ ok: false, error: err.message });
  }
}

$("#buildPayload").addEventListener("click", () => postCsv("/api/remote-config/build-payload-csv", "build"));
$("#validateRemoteConfig").addEventListener("click", () => postCsv("/api/remote-config/validate-csv", "api"));
$("#previewRemoteConfig").addEventListener("click", () => postCsv("/api/remote-config/preview-csv", "api"));

$("#remoteConfigForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  await postCsv("/api/remote-config/run-csv", "api");
});

$("#clearResults").addEventListener("click", () => {
  $("#resultsBody").innerHTML = `<tr><td colspan="5" class="empty">No results yet.</td></tr>`;
  if ($("#reviewBody")) $("#reviewBody").innerHTML = `<tr><td colspan="6" class="empty">Build payload first to review final CSV changes.</td></tr>`;
  if ($("#payloadReviewJson")) $("#payloadReviewJson").textContent = "[]";
  $("#summary").innerHTML = "";
  $("#rawJson").textContent = "{}";
});

$("#listJobs").addEventListener("click", async () => {
  const from = encodeURIComponent($("#jobsFrom").value.trim());
  const to = encodeURIComponent($("#jobsTo").value.trim());
  const qs = new URLSearchParams();
  if (from) qs.set("from", decodeURIComponent(from));
  if (to) qs.set("to", decodeURIComponent(to));
  const res = await fetch(`/api/remote-config/jobs?${qs.toString()}`);
  const data = await res.json();
  renderJobRaw(data);
});

$("#getCurrentJob").addEventListener("click", async () => {
  const res = await fetch("/api/remote-config/current");
  const data = await res.json();
  renderJobRaw(data);
});

$("#getJobById").addEventListener("click", async () => {
  const jobId = $("#jobId").value.trim();
  if (!jobId) {
    renderJobRaw({ ok: false, error: "Enter a job ID first." });
    return;
  }
  const res = await fetch(`/api/remote-config/jobs/${encodeURIComponent(jobId)}`);
  const data = await res.json();
  renderJobRaw(data);
});

$("#clearJobResults").addEventListener("click", () => {
  $("#jobRawJson").textContent = "{}";
});

loadConfig();
