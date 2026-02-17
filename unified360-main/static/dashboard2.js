// dashboard2.js — Enterprise Dashboard (IST) + Professional Recent Alerts toolbar/pagination (fixed)

(() => {
  let refreshMs = 30000;
  let limit = 50;
  let timer = null;

  // Recent Alerts UI state
  let _alertsAll = [];
  let _alertsUiMounted = false;
  let _alertsSearch = "";
  let _alertsCustomer = "__ALL__";
  let _alertsPageSize = 10;
  let _alertsPage = 1;

  document.addEventListener("DOMContentLoaded", async () => {
    initControls();
    await loadSettings();
    await loadAll();
    startAutoRefresh();
  });

  /* ---------------------- INIT SECTION ---------------------- */
  function initControls() {
    const refreshEl = document.getElementById("refreshSelect");
    if (refreshEl) {
      refreshEl.addEventListener("change", (e) => {
        refreshMs = parseInt(e.target.value, 10) * 1000;
        restartAuto();
      });
    }

    const limitEl = document.getElementById("limitSelect");
    if (limitEl) {
      limitEl.addEventListener("change", (e) => {
        limit = parseInt(e.target.value, 10);
        loadRecentAlerts();
      });
    }
  }

  async function loadSettings() {
    try {
      const res = await fetch("/api/dashboard2/settings");
      if (!res.ok) return;
      const j = await res.json();

      if (j.refresh_interval) {
        refreshMs = parseInt(j.refresh_interval, 10) * 1000;
        const refreshEl = document.getElementById("refreshSelect");
        if (refreshEl) refreshEl.value = j.refresh_interval;
      }

      if (j.default_limit) {
        limit = parseInt(j.default_limit, 10);
        const limitEl = document.getElementById("limitSelect");
        if (limitEl) limitEl.value = j.default_limit;
      }
    } catch (e) {
      console.warn("settings load failed", e);
    }
  }

  function startAutoRefresh() {
    stopAutoRefresh();
    timer = setInterval(loadAll, refreshMs);
  }

  function stopAutoRefresh() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  function restartAuto() {
    stopAutoRefresh();
    startAutoRefresh();
  }

  /* ---------------------- MAIN LOAD FUNCTION ---------------------- */
  async function loadAll() {
    await Promise.all([
      loadKpiSummary(),
      loadCategoryStatus(),
      loadRecentAlerts(),
      loadHeatmap()
    ]);
  }


  /* ---------------------- CATEGORY STATUS (Server Table) ---------------------- */
  async function loadCategoryStatus() {
    try {
      // If you added fetchJson helper, use it. Otherwise swap back to fetch()/res.json()
      const j = (typeof fetchJson === "function")
        ? await fetchJson("/api/dashboard2/category-status")
        : await (await fetch("/api/dashboard2/category-status")).json();
  
      renderServersTable(j.server_customers || {});
    } catch (e) {
      console.error("category status error", e);
      const tb = document.querySelector("#serversTable tbody");
      if (tb) tb.innerHTML = `<tr><td colspan="6" class="small-muted">Data unavailable</td></tr>`;
    }
  }


  /* ---------------------- KPI SUMMARY ---------------------- */
  async function loadKpiSummary() {
    try {
      const res = await fetch("/api/dashboard2/kpi-summary");
      const data = await res.json();
      if (!data.ok) return;

      renderKpiGrid(data.summary, data.types);
      renderNocBanner(data.summary);
      if (window.lucide) lucide.createIcons();
    } catch (e) {
      console.error("KPI summary error:", e);
    }
  }

  function kpiBadge(active, total) {
    if (total === 0)      return `<span class="badge bg-secondary">Not Configured</span>`;
    if (active === total) return `<span class="badge bg-success">Healthy</span>`;
    if (active > 0)       return `<span class="badge bg-warning text-dark">Partial</span>`;
    return `<span class="badge bg-danger">Down</span>`;
  }


  function renderNocBanner(summary) {
    const el = document.getElementById("nocCriticalBanner");
    if (!el) return;
  
    const crit = parseInt(summary?.critical_active || 0, 10);
    const updated = formatNowIST();
  
    if (!crit || crit <= 0) {
      el.classList.add("d-none");
      el.innerHTML = "";
      return;
    }
  
    el.classList.remove("d-none");
    el.innerHTML = `
      <div class="noc-banner-left">
        <div class="noc-dot"></div>
        <div>
          <div class="noc-title">Active Critical Incidents</div>
          <div class="noc-sub">Immediate attention required • Updated ${escapeHtml(updated)}</div>
        </div>
      </div>
  
      <div class="d-flex align-items-center gap-3">
        <div class="noc-count">${crit}</div>
        <button class="btn btn-sm btn-outline-danger" id="nocViewAllCritBtn">View alerts</button>
      </div>
    `;
  
    const btn = document.getElementById("nocViewAllCritBtn");
    if (btn) btn.onclick = () => {
      // opens the same modal you already have for “View all” alerts
      if (typeof openAllAlertsModal === "function") openAllAlertsModal();
    };
  }

  function overallHealthMeta(pct) {
    const n = Number(pct);
    if (!Number.isFinite(n)) return { cls: "kpi-health-unknown", pill: "—", value: "--" };
  
    const val = Math.round(n);
  
    if (val >= 90) return { cls: "kpi-health-good", pill: "Excellent", value: `${val}%` };
    if (val < 50)  return { cls: "kpi-health-bad",  pill: "Critical",  value: `${val}%` };
    return { cls: "kpi-health-warn", pill: "Needs Attention", value: `${val}%` };
  }


  function renderKpiGrid(summary, types) {
    const grid = document.getElementById("kpiGrid");
    if (!grid) return;
  
    grid.innerHTML = "";
  
    const cards = [
      { summaryKey: "total_monitors",   label: "Total Monitors",   icon: "layers" },
      { summaryKey: "active_monitors",  label: "Active Monitors",  icon: "check-circle" },
      { summaryKey: "health_percent",   label: "Overall Health %", icon: "heart-pulse" },
      { summaryKey: "alerts_24h",       label: "Alerts (24h)",     icon: "bell-ring" },
      { summaryKey: "critical_active",  label: "Active Criticals", icon: "alert-octagon" },
  
      { key: "servers",  label: "Server Monitors",  icon: "server" },
      { key: "desktops", label: "Desktop Monitors", icon: "monitor" },
      { key: "proxy",    label: "Proxy Servers",    icon: "monitor-smartphone" },
      { key: "ping",     label: "Ping Monitors",    icon: "activity" },
      { key: "port",     label: "Port Monitors",    icon: "wifi" },
      { key: "url",      label: "URL Monitors",     icon: "globe" },
      { key: "snmp",     label: "SNMP Monitors",    icon: "cpu" },
      { key: "idrac",    label: "iDRAC Monitors",   icon: "server-cog" },
      { key: "link",     label: "Link Monitors",    icon: "link" }
    ];
  
    for (const card of cards) {
      let total = 0, active = 0;
      let displayHtml = "";
      let extraClasses = [];
      let dataAttrs = "";
  
      // Critical tile pulse (keep your existing behavior)
      const isCritTile = (card.summaryKey === "critical_active");
      const critVal = parseInt(summary?.critical_active || 0, 10);
      const critClass = (isCritTile && critVal > 0)
        ? "kpi-critical kpi-critical-pulse"
        : (isCritTile ? "kpi-critical" : "");
  
      // ---- SUMMARY KPIs ----
      if (card.summaryKey) {
        const v = summary?.[card.summaryKey];
  
        // ✅ Overall Health: add color class + pill
        if (card.summaryKey === "health_percent") {
          const meta = overallHealthMeta(v);
          extraClasses.push("kpi-overall-health", meta.cls);
          dataAttrs += ` data-health="${escapeHtml(String(meta.value || ""))}"`;
  
          displayHtml = `
            <span id="overallHealthValue">${escapeHtml(meta.value)}</span>
            <span class="overall-health-pill" id="overallHealthPill">${escapeHtml(meta.pill)}</span>
          `;
        } else {
          const safeV = Number.isFinite(Number(v)) ? String(v) : "0";
          displayHtml = escapeHtml(safeV);
        }
      }
  
      // ---- MONITOR TILES (x/y) ----
      else {
        total  = types?.[card.key]?.total  || 0;
        active = types?.[card.key]?.active || 0;
  
        const isZero = (total === 0 && active === 0);
        extraClasses.push("monitor-tile");
        dataAttrs += ` data-active="${active}" data-total="${total}" data-type="${escapeHtml(card.key)}"`;
  
        if (isZero) {
          // ✅ grey/blur in CSS via class
          extraClasses.push("kpi-zero");
          displayHtml = `${active}/${total} <span class="badge bg-secondary">Not Configured</span>`;
        } else {
          displayHtml = `${active}/${total} ${kpiBadge(active, total)}`;
        }
      }
  
      grid.insertAdjacentHTML(
        "beforeend",
        `
        <div class="card shadow-sm hover-card ${critClass} ${extraClasses.join(" ")}" ${dataAttrs}>
          <div class="fs-1 mb-2"><i data-lucide="${card.icon}"></i></div>
  
          <h3 class="m-0">${displayHtml}</h3>
  
          <p class="text-muted mb-0">${escapeHtml(card.label)}</p>
        </div>
        `
      );
    }
  }


  function renderServersTable(serversObj) {
    const tbody = document.querySelector("#serversTable tbody");
    if (!tbody) return;

    tbody.innerHTML = "";

    const arr = Object.entries(serversObj).map(([cust, info]) => {
      const s = info.Servers || {};
      const total = s.total || 0;
      const active = s.active || 0;
      const down = total - active;
      const health = total ? Math.round((active / total) * 100) : 100;
      return { cust, total, active, down, health };
    });

    arr.sort((a, b) => a.health - b.health || b.down - a.down);

    arr.forEach(it => {
      tbody.insertAdjacentHTML(
        "beforeend",
        `
        <tr>
          <td><strong>${escapeHtml(it.cust)}</strong><div class="small-muted">Server Customer</div></td>
          <td>${it.total}</td>
          <td>${it.active}</td>
          <td>${it.down}</td>
          <td><span class="health-pill ${healthClass(it.health)}">${it.health}%</span></td>
          <td>
            <button class="details-btn" data-customer="${escapeHtml(it.cust)}">Details</button>
          </td>
        </tr>
        `
      );
    });

    attachDetailsButtons();
  }

  function healthClass(h) {
    if (h >= 80) return "health-good";
    if (h >= 40) return "health-warn";
    return "health-bad";
  }

  /* ---------------------- MODAL DETAILS ---------------------- */
  function attachDetailsButtons() {
    document.querySelectorAll(".details-btn").forEach(btn => {
      btn.onclick = () => openDetailsModal(btn.dataset.customer);
    });
  }

  async function openDetailsModal(customer) {
    try {
      const res = await fetch(`/api/dashboard2/recent-alerts?limit=200`);
      const alerts = await res.json();
      const list = Array.isArray(alerts)
        ? alerts.filter(a => (a.customer || "Backend") === customer)
        : [];

      const html = list.length
        ? list.map(a => `
            <div style="padding:10px;border-bottom:1px solid rgba(0,0,0,0.06)">
              <strong>${escapeHtml(a.type || "Alert")} • ${escapeHtml(a.device || "-")}</strong>
              <div class="small-muted">${escapeHtml(formatAlertTsIST(a.ts))}</div>
            </div>
          `).join("")
        : `<div class="small-muted">No active critical alerts</div>`;

      const existing = document.getElementById("customerModal");
      if (existing) existing.remove();

      document.body.insertAdjacentHTML(
        "beforeend",
        `
        <div class="modal fade" id="customerModal" tabindex="-1">
          <div class="modal-dialog modal-lg">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title">${escapeHtml(customer)}</h5>
                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
              </div>
              <div class="modal-body">${html}</div>
            </div>
          </div>
        </div>
        `
      );

      if (window.bootstrap && bootstrap.Modal) {
        new bootstrap.Modal(document.getElementById("customerModal")).show();
      }
    } catch (e) {
      console.error(e);
    }
  }

  /* ---------------------- RECENT ALERTS (Professional UI) ---------------------- */
  async function loadRecentAlerts() {
    try {
      const res = await fetch(`/api/dashboard2/recent-alerts?limit=${limit}`);
      const list = await res.json();
      _alertsAll = Array.isArray(list) ? list : [];

      if (!_alertsUiMounted) {
        mountRecentAlertsUI();
        _alertsUiMounted = true;
      }

      renderRecentAlerts();

      const updatedEl = document.getElementById("alertsUpdated");
      if (updatedEl) updatedEl.textContent = "Updated: " + formatNowIST();
    } catch (e) {
      console.error("alerts error", e);
    }
  }

  function mountRecentAlertsUI() {
      const container = document.getElementById("alertsFeed");
      if (!container) return;
    
      // Default to 5 items on dashboard
      _alertsPageSize = 5;
    
      container.innerHTML = `
        <div class="alerts-toolbar">
          <div class="row g-2 align-items-center">
            <div class="col-12 col-lg-7">
              <input id="alertsSearch" class="form-control form-control-sm"
                     placeholder="Search device / type / customer…" />
            </div>
    
            <div class="col-6 col-lg-3">
              <select id="alertsCustomerFilter" class="form-select form-select-sm w-100">
                <option value="__ALL__">All Customers</option>
              </select>
            </div>
    
            <div class="col-6 col-lg-2 d-grid">
              <button id="alertsViewAll" class="btn btn-sm btn-outline-primary">View all</button>
            </div>
    
            <div class="col-12 d-flex align-items-center justify-content-between mt-1">
              <div id="alertsRangeInfo" class="small text-muted">Showing 0 of 0</div>
            </div>
          </div>
        </div>
    
        <div id="alertsList" class="alerts-list"></div>
      `;
    
      const searchEl = document.getElementById("alertsSearch");
      const custEl = document.getElementById("alertsCustomerFilter");
      const viewAllEl = document.getElementById("alertsViewAll");
    
      if (searchEl) {
        searchEl.value = _alertsSearch;
        searchEl.addEventListener("input", (e) => {
          _alertsSearch = e.target.value || "";
          renderRecentAlerts();
        });
      }
    
      if (custEl) {
        custEl.value = _alertsCustomer;
        custEl.addEventListener("change", (e) => {
          _alertsCustomer = e.target.value || "__ALL__";
          renderRecentAlerts();
        });
      }
    
      if (viewAllEl) {
        viewAllEl.addEventListener("click", () => openAllAlertsModal());
      }
    }
  
    function renderRecentAlerts() {
    const listEl = document.getElementById("alertsList");
    const rangeInfoEl = document.getElementById("alertsRangeInfo");
    const custEl = document.getElementById("alertsCustomerFilter");
    if (!listEl) return;
  
    // Populate customer dropdown
    if (custEl) {
      const current = _alertsCustomer || "__ALL__";
      const customers = Array.from(new Set(_alertsAll.map(a => (a.customer || "Backend"))))
        .sort((a, b) => a.localeCompare(b));
  
      custEl.innerHTML =
        [`<option value="__ALL__">All Customers</option>`]
          .concat(customers.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`))
          .join("");
  
      custEl.value = customers.includes(current) ? current : "__ALL__";
      _alertsCustomer = custEl.value;
    }
  
    const filtered = applyAlertsFilter(_alertsAll);
  
    if (!filtered.length) {
      listEl.innerHTML = `<div class="small-muted">No active critical alerts</div>`;
      if (rangeInfoEl) rangeInfoEl.textContent = `Showing 0 of 0`;
      return;
    }
  
    const top = filtered.slice(0, _alertsPageSize);
    if (rangeInfoEl) rangeInfoEl.textContent = `Showing 1–${top.length} of ${filtered.length}`;
  
    listEl.innerHTML = "";
    top.forEach(a => {
      const cust = a.customer || "Backend";
      const tsText = formatAlertTsIST(a.ts);
  
      listEl.insertAdjacentHTML(
        "beforeend",
        `
        <div class="alert-row">
          <div class="alert-badge">Critical</div>
  
          <div class="alert-body">
            <div class="alert-device">${escapeHtml(a.device || "-")}</div>
            <div class="alert-type">${escapeHtml(a.type || "Alert")}</div>
            <div class="alert-meta">${escapeHtml(cust)} · ${escapeHtml(tsText)}</div>
          </div>
        </div>
        `
      );
    });
  }

  function openAllAlertsModal() {
    const existing = document.getElementById("allAlertsModal");
    if (existing) existing.remove();
  
    const filtered = applyAlertsFilter(_alertsAll);
    const rows = filtered.map(a => {
      const cust = a.customer || "Backend";
      return `
        <div style="padding:10px;border-bottom:1px solid rgba(0,0,0,0.06)">
          <div style="font-weight:600">${escapeHtml(a.device || "-")} <span class="text-muted">• ${escapeHtml(a.type || "Alert")}</span></div>
          <div class="small text-muted">${escapeHtml(cust)} · ${escapeHtml(formatAlertTsIST(a.ts))}</div>
        </div>
      `;
    }).join("") || `<div class="small-muted">No alerts</div>`;
  
    document.body.insertAdjacentHTML(
      "beforeend",
      `
      <div class="modal fade" id="allAlertsModal" tabindex="-1">
        <div class="modal-dialog modal-lg modal-dialog-scrollable">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title">All Critical Alerts</h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body p-0">${rows}</div>
          </div>
        </div>
      </div>
      `
    );
  
    if (window.bootstrap && bootstrap.Modal) {
      new bootstrap.Modal(document.getElementById("allAlertsModal")).show();
    }
  }

  function applyAlertsFilter(list) {
    const q = (_alertsSearch || "").trim().toLowerCase();
    const custFilter = _alertsCustomer || "__ALL__";

    return (list || []).filter(a => {
      const cust = (a.customer || "Backend");
      if (custFilter !== "__ALL__" && cust !== custFilter) return false;

      if (!q) return true;
      const hay = `${a.type || ""} ${a.device || ""} ${cust} ${a.source || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }

  /* ---------------------- HEATMAP ---------------------- */
  async function loadHeatmap() {
    try {
      const res = await fetch("/api/dashboard2/heatmap");
      const data = await res.json();
      drawHeatmap(data);
    } catch (e) {
      console.error("heatmap error", e);
    }
  }

  function drawHeatmap(data) {
    const canvas = document.getElementById("heatmapCanvas");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const W = canvas.clientWidth, H = canvas.clientHeight;
    const scale = devicePixelRatio || 1;
    canvas.width = W * scale;
    canvas.height = H * scale;
    ctx.scale(scale, scale);

    ctx.clearRect(0, 0, W, H);

    const labels = data.timestamps || [];
    const cats = data.categories || [];
    const matrix = data.matrix || [];

    const rows = cats.length;
    const cols = labels.length;

    const labelW = 90;
    const cellW = Math.max(6, Math.floor((W - labelW) / Math.max(1, cols)));
    const cellH = Math.max(16, Math.floor(H / Math.max(1, rows)));

    ctx.font = "12px sans-serif";
    ctx.textBaseline = "middle";

    // Category labels
    cats.forEach((name, r) => {
      ctx.fillStyle = "#333";
      ctx.fillText(name, 6, r * cellH + cellH / 2);
    });

    // Heat cells (2 = red, else green)
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const v = matrix[r]?.[c];
        ctx.fillStyle = v === 2 ? "#dc3545" : "#28a745";
        ctx.fillRect(labelW + c * cellW + 1, r * cellH + 1, cellW - 2, cellH - 2);
      }
    }
  }

  /* ---------------------- TIME HELPERS (UTC → IST) ---------------------- */
  function formatNowIST() {
    try {
      return new Date().toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        hour12: true,
        year: "numeric",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      });
    } catch {
      return new Date().toLocaleString();
    }
  }

  // Robust timestamp formatter:
  // - null/undefined -> "—"
  // - ISO with Z/+00:00/-05:00 -> parse directly
  // - naive ISO -> assume UTC by appending Z
  function formatAlertTsIST(ts) {
    if (!ts) return "—";

    const s = String(ts).trim();
    if (!s || s.toLowerCase() === "null" || s.toLowerCase() === "none") return "—";

    // Already timezone-aware?
    const hasTZ = /([zZ]$)|([+-]\d{2}:\d{2}$)/.test(s);
    const safe = hasTZ ? s : (s + "Z");

    const d = new Date(safe);
    if (isNaN(d.getTime())) return "—";

    try {
      return d.toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        hour12: true,
        year: "numeric",
        month: "numeric",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      });
    } catch {
      return d.toLocaleString();
    }
  }

  /* ---------------------- MISC HELPERS ---------------------- */
  function escapeHtml(s) {
    return (s || "").replace(/[&<>"'`=\/]/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;", "/": "&#x2F;",
      "`": "&#x60;", "=": "&#x3D;"
    }[c]));
  }

  // expose manual reload if needed
  window.dashboard2_loadAll = loadAll;
})();

