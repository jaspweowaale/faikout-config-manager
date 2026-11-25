let HOSTS = [];           // loaded dynamically from /api/devices -- never hardcoded
let currentFilter = "all";
let selectedCells = {};   // key -> Set(hostnames)
let matrixData = null;
let devicesData = [];

function $(sel) { return document.querySelector(sel); }
function el(tag, attrs, children) {
  const e = document.createElement(tag);
  for (const k in (attrs || {})) {
    if (k === "text") e.textContent = attrs[k];
    else if (k === "html") e.innerHTML = attrs[k];
    else e.setAttribute(k, attrs[k]);
  }
  (children || []).forEach(c => e.appendChild(c));
  return e;
}

async function fetchJSON(url, opts) {
  const r = await fetch(url, opts);
  return r.json();
}

// ---------- tabs ----------
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.add("hidden"));
    document.getElementById("tab-" + btn.dataset.tab).classList.remove("hidden");
    if (btn.dataset.tab === "drift") loadDrift();
    if (btn.dataset.tab === "profiles") loadProfiles();
    if (btn.dataset.tab === "backups") loadBackups();
    if (btn.dataset.tab === "history") loadHistory();
    if (btn.dataset.tab === "devices") loadDevices();
    if (btn.dataset.tab === "mqtt") loadMqttSettings();
  });
});

// ---------- status banner ----------
async function loadStatus() {
  const s = await fetchJSON("/api/status");
  const banner = $("#write-banner");
  if (s.live_writes_enabled) {
    banner.textContent = "Live writes are ENABLED in this installation. Sync/restore/profile-apply will actually change controller settings.";
    banner.classList.add("banner-danger");
  } else {
    banner.textContent = "Live writes are DISABLED (default). Read-only retrieval, comparison, and backups work; sync/restore/profile-apply will be refused. Set ENABLE_LIVE_WRITES=true to change this.";
  }
}

// ---------- connection summary ----------
async function loadConnection() {
  const conn = await fetchJSON("/api/connection");
  const container = $("#connection-summary");
  container.innerHTML = "";
  HOSTS.forEach(h => {
    const c = conn[h];
    if (!c) return;
    const ageText = c.last_success_age_s == null ? "never" : `${Math.round(c.last_success_age_s)}s ago`;
    container.appendChild(el("span", {
      class: "conn-badge conn-" + c.status,
      text: `${h}: ${c.status} (last ok ${ageText})`
    }));
  });
}

// ---------- devices ----------
async function loadDevices() {
  devicesData = await fetchJSON("/api/devices");
  HOSTS = devicesData.map(d => d.hostname);
  renderDevicesTable();
}

function renderDevicesTable() {
  const body = $("#devices-body");
  body.innerHTML = "";
  devicesData.forEach(d => {
    const tr = el("tr", {}, [
      el("td", { text: d.hostname }),
      el("td", { text: d.label || "" }),
      el("td", { text: d.physical_location || "" }),
      el("td", { text: d.real_model || "" }),
      el("td", { text: d.real_capacity_hp ? `${d.real_capacity_hp} HP` : "" }),
      el("td", { text: d.mac || "" }),
      el("td", { text: d.added_via }),
    ]);
    const removeBtn = el("button", { text: "Remove" });
    removeBtn.addEventListener("click", async () => {
      if (!confirm(`Remove ${d.hostname} from the registry? (does not touch the device itself)`)) return;
      await fetch(`/api/devices/${encodeURIComponent(d.hostname)}`, { method: "DELETE" });
      await loadDevices();
    });
    const td = el("td", {});
    td.appendChild(removeBtn);
    tr.appendChild(td);
    body.appendChild(tr);
  });
}

$("#add-device-btn").addEventListener("click", async () => {
  const hostname = $("#add-hostname").value.trim();
  if (!hostname) { alert("hostname is required"); return; }
  const resp = await fetchJSON("/api/devices", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      hostname, label: $("#add-label").value, physical_location: $("#add-location").value,
      real_model: $("#add-model").value,
    })
  });
  $("#add-device-result").textContent = JSON.stringify(resp);
  $("#add-hostname").value = ""; $("#add-label").value = "";
  $("#add-location").value = ""; $("#add-model").value = "";
  await loadDevices();
});

$("#discover-btn").addEventListener("click", async () => {
  $("#discover-results").innerHTML = "<li>Scanning (a few seconds)...</li>";
  const resp = await fetchJSON("/api/discover", { method: "POST" });
  const list = $("#discover-results");
  list.innerHTML = "";
  if (resp.error) { list.appendChild(el("li", { text: "Error: " + resp.error })); return; }
  if (resp.length === 0) { list.appendChild(el("li", { text: "No new controllers found." })); return; }
  resp.forEach(c => {
    const li = el("li", { text: `${c.hostname} (mac ${c.mac || "unknown"}) ` });
    const addBtn = el("button", { text: "Add" });
    addBtn.addEventListener("click", async () => {
      await fetchJSON("/api/devices", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hostname: c.hostname, mac: c.mac, added_via: "discovery" })
      });
      await loadDevices();
      li.textContent = `${c.hostname} -- added`;
    });
    li.appendChild(addBtn);
    list.appendChild(li);
  });
});

// ---------- MQTT settings ----------
async function loadMqttSettings() {
  const s = await fetchJSON("/api/mqtt-settings");
  $("#mqtt-host").value = s.host || "";
  $("#mqtt-port").value = s.port || 1883;
  $("#mqtt-username").value = s.username || "";
  $("#mqtt-tls").checked = !!s.tls;
  $("#mqtt-status").textContent = s.configured ? "Configured." : "Not configured yet.";
}

$("#mqtt-save-btn").addEventListener("click", async () => {
  const body = {
    host: $("#mqtt-host").value, port: parseInt($("#mqtt-port").value, 10) || 1883,
    username: $("#mqtt-username").value, tls: $("#mqtt-tls").checked,
  };
  const pw = $("#mqtt-password").value;
  if (pw) body.password = pw;
  const resp = await fetchJSON("/api/mqtt-settings", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });
  $("#mqtt-result").textContent = JSON.stringify(resp);
  $("#mqtt-password").value = "";
  await loadMqttSettings();
});

$("#mqtt-test-btn").addEventListener("click", async () => {
  $("#mqtt-result").textContent = "Testing...";
  const resp = await fetchJSON("/api/mqtt-settings/test", { method: "POST" });
  $("#mqtt-result").textContent = resp.ok ? "Connection OK." : ("Failed: " + resp.error);
});

// ---------- matrix ----------
document.querySelectorAll(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    loadMatrix();
  });
});

async function loadMatrix() {
  matrixData = await fetchJSON("/api/matrix?filter=" + currentFilter);
  HOSTS = matrixData.hostnames;
  $("#no-devices-message").classList.toggle("hidden", HOSTS.length > 0);
  renderDeviceHeaders();
  renderMatrixHead();
  renderMatrixBody();
}

function renderDeviceHeaders() {
  const container = $("#device-headers");
  container.innerHTML = "";
  if (HOSTS.length === 0) return;
  container.appendChild(el("div", { class: "corner" }));
  HOSTS.forEach(h => {
    const d = devicesData.find(x => x.hostname === h) || {};
    const card = el("div", { class: "device-card", html:
      `<div class="loc">${d.physical_location || h}</div>` +
      `<div>${h}${d.real_model ? " &rarr; <b>" + d.real_model + "</b>" : ""}</div>` +
      (d.real_capacity_hp ? `<div>${d.real_capacity_hp} HP</div>` : "")
    });
    container.appendChild(card);
  });
}

function renderMatrixHead() {
  const row = $("#matrix-head-row");
  row.innerHTML = "";
  row.appendChild(el("th", { text: "Setting (plane / flags)" }));
  HOSTS.forEach(h => {
    const th = el("th", {});
    const cb = el("input", { type: "checkbox", "data-col": h });
    cb.addEventListener("change", () => toggleColumnSelection(h, cb.checked));
    th.appendChild(cb);
    th.appendChild(document.createTextNode(" " + h));
    row.appendChild(th);
  });
}

function renderMatrixBody() {
  const body = $("#matrix-body");
  body.innerHTML = "";
  matrixData.rows.forEach(row => {
    const tr = el("tr", { class: "plane-" + row.plane + (row.is_protected ? " row-protected" : "") +
                            (!row.is_uniform ? " row-nonuniform" : "") });
    const keyCell = el("td", { class: "setting-key" });
    const rowCb = el("input", { type: "checkbox", "data-row": row.key });
    rowCb.addEventListener("change", () => toggleRowSelection(row.key, rowCb.checked));
    keyCell.appendChild(rowCb);
    keyCell.appendChild(document.createTextNode(" " + row.key));
    if (row.is_protected) keyCell.appendChild(el("span", { class: "badge badge-protected", text: "PROTECTED" }));
    if (row.do_not_standardize) keyCell.appendChild(el("span", { class: "badge badge-dns", text: "DO-NOT-STANDARDIZE" }));
    const details = el("details", { class: "desc" });
    details.appendChild(el("summary", { text: "details" }));
    details.appendChild(el("p", { text: row.description }));
    keyCell.appendChild(details);
    tr.appendChild(keyCell);

    HOSTS.forEach(h => {
      const val = row.values[h];
      const td = el("td", {});
      if (!row.editable || val === "<absent>") {
        td.appendChild(el("span", { class: "cell-readonly", text: String(val) }));
      } else {
        const wrap = el("div", { class: "cell-editable" });
        const cb = el("input", { type: "checkbox" });
        cb.addEventListener("change", () => toggleCellSelection(row.key, h, cb.checked));
        const input = el("input", { type: "text", value: String(val) });
        input.dataset.key = row.key;
        input.dataset.host = h;
        wrap.appendChild(cb);
        wrap.appendChild(input);
        td.appendChild(wrap);
      }
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

function toggleCellSelection(key, host, on) {
  if (!selectedCells[key]) selectedCells[key] = new Set();
  if (on) selectedCells[key].add(host); else selectedCells[key].delete(host);
  updateSelectionCount();
}
function toggleRowSelection(key, on) {
  if (!selectedCells[key]) selectedCells[key] = new Set();
  HOSTS.forEach(h => { if (on) selectedCells[key].add(h); else selectedCells[key].delete(h); });
  updateSelectionCount();
}
function toggleColumnSelection(host, on) {
  (matrixData.rows || []).forEach(row => {
    if (!row.editable) return;
    if (!selectedCells[row.key]) selectedCells[row.key] = new Set();
    if (on) selectedCells[row.key].add(host); else selectedCells[row.key].delete(host);
  });
  updateSelectionCount();
}
function updateSelectionCount() {
  let n = 0;
  Object.values(selectedCells).forEach(s => n += s.size);
  $("#selection-count").textContent = n + " cells selected";
}

// ---------- refresh (read-only live) ----------
$("#refresh-btn").addEventListener("click", async () => {
  const force = $("#refresh-force").checked;
  $("#refresh-btn").textContent = "Refreshing...";
  const result = await fetchJSON("/api/refresh", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hostnames: HOSTS, force })
  });
  $("#refresh-btn").textContent = "Refresh selected from live (read-only)";
  await loadConnection();
  await loadMatrix();
  console.log("refresh result", result);
});

// ---------- sync preview ----------
$("#preview-sync-btn").addEventListener("click", async () => {
  const cells = [];
  Object.entries(selectedCells).forEach(([key, hosts]) => {
    hosts.forEach(h => {
      const input = document.querySelector(`input[data-key="${key}"][data-host="${h}"]`);
      const value = input ? input.value : null;
      if (value !== null) cells.push({ key, target: h, value });
    });
  });
  if (cells.length === 0) { alert("select at least one cell first"); return; }
  const preview = await fetchJSON("/api/sync/preview", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cells })
  });
  renderPreview(preview, cells);
});

let _pendingApplyCells = [];
function renderPreview(preview, cells) {
  _pendingApplyCells = cells;
  const body = $("#preview-body");
  body.innerHTML = "";
  preview.rows.forEach(r => {
    body.appendChild(el("tr", {}, [
      el("td", { text: r.target }), el("td", { text: r.key }),
      el("td", { text: String(r.current_value) }), el("td", { text: String(r.new_value) }),
      el("td", { text: r.would_change ? "yes" : "no change" }),
    ]));
  });
  $("#preview-problems").textContent = preview.problems.length ?
    "Refused: " + preview.problems.join(" | ") : "";
  $("#apply-result").textContent = "";
  $("#preview-modal").classList.remove("hidden");
}
$("#close-preview-btn").addEventListener("click", () => $("#preview-modal").classList.add("hidden"));
$("#apply-btn").addEventListener("click", async () => {
  const resp = await fetchJSON("/api/sync/apply", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cells: _pendingApplyCells })
  });
  $("#apply-result").textContent = resp.error ? resp.error : JSON.stringify(resp);
});

// ---------- drift ----------
async function loadDrift() {
  const d = await fetchJSON("/api/drift");
  $("#baseline-json").textContent = JSON.stringify(d.baseline, null, 2);
  const body = $("#drift-body");
  body.innerHTML = "";
  if (d.findings.length === 0) {
    body.appendChild(el("tr", {}, [el("td", { colspan: "5", text: "No drift detected." })]));
  }
  d.findings.forEach(f => {
    body.appendChild(el("tr", {}, [
      el("td", { text: f.severity }), el("td", { text: f.hostname }), el("td", { text: f.key }),
      el("td", { text: String(f.live_value) }), el("td", { text: String(f.expected_value) }),
    ]));
  });
  const ota = await fetchJSON("/api/ota-status");
  const otaBody = $("#ota-status-body");
  otaBody.innerHTML = "";
  Object.entries(ota).forEach(([h, v]) => {
    otaBody.appendChild(el("tr", {}, [
      el("td", { text: h }), el("td", { text: String(v.ota_auto) }),
      el("td", { text: v.protected ? "yes" : "NO" }),
    ]));
  });
}

// ---------- profiles ----------
async function loadProfiles() {
  const names = await fetchJSON("/api/profiles");
  const list = $("#profile-list");
  list.innerHTML = "";
  names.forEach(n => list.appendChild(el("li", { text: n })));
}
$("#save-profile-btn").addEventListener("click", async () => {
  const name = $("#profile-name").value.trim();
  if (!name) { alert("name required"); return; }
  const settings = {};
  Object.entries(selectedCells).forEach(([key, hosts]) => {
    if (hosts.size === 0) return;
    const anyHost = [...hosts][0];
    const input = document.querySelector(`input[data-key="${key}"][data-host="${anyHost}"]`);
    if (input) settings[key] = input.value;
  });
  const resp = await fetchJSON("/api/profiles", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, settings, description: $("#profile-desc").value })
  });
  $("#profile-save-result").textContent = JSON.stringify(resp);
  loadProfiles();
});

// ---------- backups ----------
async function loadBackups() {
  const data = await fetchJSON("/api/backups");
  const container = $("#backups-list");
  container.innerHTML = "";
  HOSTS.forEach(h => {
    const box = el("div", {});
    box.appendChild(el("h3", { text: h }));
    const btn = el("button", { text: "Back up now (read-only query)" });
    btn.addEventListener("click", async () => {
      await fetchJSON(`/api/backups/${h}`, { method: "POST" });
      loadBackups();
    });
    box.appendChild(btn);
    const ul = el("ul");
    (data[h] || []).forEach(name => ul.appendChild(el("li", { text: name })));
    box.appendChild(ul);
    container.appendChild(box);
  });
}

// ---------- history ----------
async function loadHistory() {
  const entries = await fetchJSON("/api/history");
  const body = $("#history-body");
  body.innerHTML = "";
  entries.forEach(e => {
    body.appendChild(el("tr", {}, [
      el("td", { text: new Date(e.timestamp * 1000).toISOString() }),
      el("td", { text: e.operation }), el("td", { text: e.hostname }), el("td", { text: e.key }),
      el("td", { text: String(e.old_value) }), el("td", { text: String(e.new_value) }),
      el("td", { text: String(e.verified) }), el("td", { text: String(e.rolled_back) }),
    ]));
  });
}

// ---------- init ----------
(async function init() {
  await loadStatus();
  await loadDevices();
  await loadConnection();
  await loadMatrix();

  // Demo/screenshot convenience only: #tab=NAME switches tabs on load,
  // #tab=matrix&demo=preview also selects one cell and opens the dry-run
  // preview modal. Purely a navigation convenience.
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const tab = params.get("tab");
  if (tab) {
    const btn = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
    if (btn) btn.click();
  }
  if (params.get("demo") === "preview") {
    setTimeout(() => {
      const cb = document.querySelector('.cell-editable input[type=checkbox]');
      if (cb) {
        cb.checked = true;
        cb.dispatchEvent(new Event("change"));
        document.getElementById("preview-sync-btn").click();
      }
    }, 300);
  }
})();
