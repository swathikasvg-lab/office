document.addEventListener("DOMContentLoaded", () => {
  const state = {
    customers: [],
    apps: [],
    monitorTypes: [],
    selectedCustomerId: "",
    selectedAppId: null,
    selectedServiceId: null,
    dashboard: null,
    sharedLayout: null,
    layoutSaveTimer: null,
    cy: null,
    itamDependencySuggestions: [],
  };

  const el = {
    msg: document.getElementById("itomMsg"),
    customerFilter: document.getElementById("customerFilter"),
    refreshBtn: document.getElementById("refreshBtn"),
    openAppModalBtn: document.getElementById("openAppModalBtn"),
    appCount: document.getElementById("appCount"),
    appsTbody: document.getElementById("appsTbody"),
    selectedAppTitle: document.getElementById("selectedAppTitle"),
    selectedAppMeta: document.getElementById("selectedAppMeta"),
    appHealthBadge: document.getElementById("appHealthBadge"),
    servicesTbody: document.getElementById("servicesTbody"),
    bindingsTbody: document.getElementById("bindingsTbody"),
    depsTbody: document.getElementById("depsTbody"),
    runBindingQualityBtn: document.getElementById("runBindingQualityBtn"),
    runBindingSuggestBtn: document.getElementById("runBindingSuggestBtn"),
    bindingQualitySummary: document.getElementById("bindingQualitySummary"),
    bindingQualityList: document.getElementById("bindingQualityList"),
    bindingSuggestionList: document.getElementById("bindingSuggestionList"),
    runItamDepSuggestBtn: document.getElementById("runItamDepSuggestBtn"),
    applyItamDepSuggestBtn: document.getElementById("applyItamDepSuggestBtn"),
    itamDepSuggestSummary: document.getElementById("itamDepSuggestSummary"),
    itamDepSuggestList: document.getElementById("itamDepSuggestList"),
    impactTitle: document.getElementById("impactTitle"),
    impactMeta: document.getElementById("impactMeta"),
    impactList: document.getElementById("impactList"),
    graph: document.getElementById("dependencyGraph"),
    graphFilter: document.getElementById("graphFilter"),
    toggleBindings: document.getElementById("toggleBindings"),
    saveLayoutBtn: document.getElementById("saveLayoutBtn"),
    resetLayoutBtn: document.getElementById("resetLayoutBtn"),
    kpiApps: document.getElementById("kpiApps"),
    kpiServices: document.getElementById("kpiServices"),
    kpiImpacted: document.getElementById("kpiImpacted"),
    kpiDown: document.getElementById("kpiDown"),
    itamCoverageKpi: document.getElementById("itamCoverageKpi"),
    itamBindingKpi: document.getElementById("itamBindingKpi"),
    itamGapKpi: document.getElementById("itamGapKpi"),

    appCustomerId: document.getElementById("appCustomerId"),
    appForm: document.getElementById("appForm"),
    serviceForm: document.getElementById("serviceForm"),
    bindingForm: document.getElementById("bindingForm"),
    depForm: document.getElementById("depForm"),
    openServiceModalBtn: document.getElementById("openServiceModalBtn"),
    openBindingModalBtn: document.getElementById("openBindingModalBtn"),
    openDepModalBtn: document.getElementById("openDepModalBtn"),

    bindingServiceId: document.getElementById("bindingServiceId"),
    bindingMonitorType: document.getElementById("bindingMonitorType"),
    bindingMonitorRef: document.getElementById("bindingMonitorRef"),
    depParentId: document.getElementById("depParentId"),
    depChildId: document.getElementById("depChildId"),
  };

  const appModal = bootstrap.Modal.getOrCreateInstance(document.getElementById("appModal"));
  const serviceModal = bootstrap.Modal.getOrCreateInstance(document.getElementById("serviceModal"));
  const bindingModal = bootstrap.Modal.getOrCreateInstance(document.getElementById("bindingModal"));
  const depModal = bootstrap.Modal.getOrCreateInstance(document.getElementById("depModal"));

  function showMsg(text, kind = "success") {
    el.msg.className = `alert py-2 alert-${kind}`;
    el.msg.textContent = text;
    el.msg.classList.remove("d-none");
    window.setTimeout(() => el.msg.classList.add("d-none"), 3500);
  }

  function statusClass(status) {
    const s = String(status || "").toUpperCase();
    if (s === "DOWN") return "text-bg-danger";
    if (s === "IMPACTED") return "text-bg-warning";
    if (s === "DEGRADED") return "text-bg-info";
    return "text-bg-success";
  }

  function layoutStorageKey(appId) {
    return `itom:layout:${appId}`;
  }

  function saveCurrentLayout(appId) {
    if (!state.cy || !appId) return;
    const positions = {};
    state.cy.nodes().forEach((n) => {
      positions[n.id()] = n.position();
    });
    localStorage.setItem(layoutStorageKey(appId), JSON.stringify(positions));
    schedulePersistLayout(appId);
  }

  function loadSavedLayout(appId) {
    if (!appId) return null;
    const raw = localStorage.getItem(layoutStorageKey(appId));
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function resetSavedLayout(appId) {
    if (!appId) return;
    localStorage.removeItem(layoutStorageKey(appId));
  }

  function currentLayoutPayload() {
    if (!state.cy) return {};
    const out = {};
    state.cy.nodes().forEach((n) => {
      out[n.id()] = n.position();
    });
    return out;
  }

  async function loadSharedLayout(appId) {
    if (!appId) return {};
    const data = await api(`/api/itom/applications/${appId}/layout`);
    return data.layout || {};
  }

  async function persistLayout(appId) {
    if (!appId || !state.cy) return;
    const layout = currentLayoutPayload();
    await api(`/api/itom/applications/${appId}/layout`, {
      method: "POST",
      body: JSON.stringify({ layout }),
    });
  }

  function schedulePersistLayout(appId) {
    if (!appId) return;
    if (state.layoutSaveTimer) window.clearTimeout(state.layoutSaveTimer);
    state.layoutSaveTimer = window.setTimeout(async () => {
      try {
        await persistLayout(appId);
      } catch (_) {
        // ignore transient save failures on drag.
      }
    }, 900);
  }

  function cyColor(status) {
    const s = String(status || "").toUpperCase();
    if (s === "DOWN") return "#df4d52";
    if (s === "IMPACTED") return "#f2b64a";
    if (s === "DEGRADED") return "#4ea0f4";
    return "#26b96f";
  }

  async function api(url, options = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || "Request failed");
    }
    return data;
  }

  function fillCustomerSelects() {
    const options = state.customers
      .map((c) => `<option value="${c.cid}">${c.acct_id} - ${c.name}</option>`)
      .join("");
    el.customerFilter.innerHTML = options || '<option value="">No customers</option>';
    el.appCustomerId.innerHTML = options || '<option value="">No customers</option>';

    if (state.customers.length > 0) {
      if (!state.selectedCustomerId) {
        state.selectedCustomerId = String(state.customers[0].cid);
      }
      el.customerFilter.value = state.selectedCustomerId;
      el.appCustomerId.value = state.selectedCustomerId;
    }
  }

  async function loadCustomers() {
    const data = await api("/api/itom/customers");
    state.customers = data.items || [];
    fillCustomerSelects();
  }

  async function loadMonitorTypes() {
    const data = await api("/api/itom/monitor-types");
    state.monitorTypes = data.items || [];
    el.bindingMonitorType.innerHTML = state.monitorTypes
      .map((x) => `<option value="${x}">${x}</option>`)
      .join("");
  }

  async function loadApplications() {
    const data = await api("/api/itom/applications");
    const allApps = data.items || [];
    const cid = String(state.selectedCustomerId || "");
    state.apps = cid ? allApps.filter((x) => String(x.customer_id) === cid) : allApps;
    el.appCount.textContent = String(state.apps.length);
    el.kpiApps.textContent = String(state.apps.length);

    if (state.selectedAppId && !state.apps.find((x) => x.id === state.selectedAppId)) {
      state.selectedAppId = null;
      state.selectedServiceId = null;
    }
    if (!state.selectedAppId && state.apps.length > 0) {
      state.selectedAppId = state.apps[0].id;
    }
    renderApplications();
    await loadSelectedDashboard();
    await loadItamCoverageSummary();
  }

  async function loadItamCoverageSummary() {
    const params = new URLSearchParams();
    if (state.selectedCustomerId) {
      params.set("customer_id", String(state.selectedCustomerId));
    }

    try {
      const data = await api(`/api/itam/coverage/summary?${params.toString()}`);
      if (el.itamCoverageKpi) {
        el.itamCoverageKpi.textContent =
          `ITAM coverage: ${Number(data.monitoring_coverage_pct || 0).toFixed(2)}% (${data.monitoring_covered_assets || 0}/${data.total_assets || 0})`;
      }
      if (el.itamBindingKpi) {
        el.itamBindingKpi.textContent =
          `ITAM binding: ${Number(data.itom_binding_pct || 0).toFixed(2)}% (${data.itom_bound_assets || 0}/${data.total_assets || 0})`;
      }
      if (el.itamGapKpi) {
        el.itamGapKpi.textContent = `ITAM gaps: ${data.monitoring_gap_assets || 0} monitoring, ${data.itom_unbound_assets || 0} unbound`;
      }
    } catch (err) {
      if (el.itamCoverageKpi) el.itamCoverageKpi.textContent = "ITAM coverage: unavailable";
      if (el.itamBindingKpi) el.itamBindingKpi.textContent = "ITAM binding: unavailable";
      if (el.itamGapKpi) el.itamGapKpi.textContent = err.message || "ITAM gaps: unavailable";
    }
  }

  function renderApplications() {
    el.appsTbody.innerHTML = "";
    if (!state.apps.length) {
      el.appsTbody.innerHTML = '<tr><td colspan="3" class="text-muted text-center py-3">No applications</td></tr>';
      return;
    }

    state.apps.forEach((app) => {
      const selectedClass = app.id === state.selectedAppId ? "table-primary" : "";
      const tr = document.createElement("tr");
      tr.className = selectedClass;
      tr.innerHTML = `
        <td>
          <div class="fw-semibold">${app.name}</div>
          <div class="small text-muted">${app.code || "-"}</div>
        </td>
        <td>${app.tier || "-"}</td>
        <td>
          <button class="btn btn-sm btn-light border select-app" data-id="${app.id}" title="Select">
            <i data-lucide="mouse-pointer-2" width="14"></i>
          </button>
          <button class="btn btn-sm btn-danger del-app" data-id="${app.id}" title="Delete">
            <i data-lucide="trash-2" width="14"></i>
          </button>
        </td>
      `;
      el.appsTbody.appendChild(tr);
    });
    if (window.lucide) lucide.createIcons();
  }

  function renderImpactDetails(item) {
    if (!item) {
      el.impactTitle.textContent = "Select a service node in graph";
      el.impactMeta.textContent = "Impact path and reasons will appear here.";
      el.impactList.innerHTML = "";
      return;
    }

    const svc = item.service;
    el.impactTitle.textContent = `${svc.name} (${item.health})`;
    el.impactMeta.textContent = `Type: ${svc.service_type || "-"} | Criticality: ${svc.criticality || "-"}`;

    const lines = [];
    (item.reasons || []).forEach((r) => {
      if (r.monitor_type && r.monitor_ref) {
        lines.push(`Binding Alert: ${r.monitor_type}:${r.monitor_ref}`);
      } else if (r.child_service_id) {
        lines.push(`Dependency Impact via service #${r.child_service_id} (${r.dependency_type})`);
      }
    });
    (item.affected_services || []).forEach((x) => {
      lines.push(`Affects upstream: ${x.service_name}`);
    });

    el.impactList.innerHTML = lines.length
      ? lines.map((x) => `<li>${x}</li>`).join("")
      : "<li>No current impact chain.</li>";
  }

  function fillServiceDropdowns(services) {
    const opts = services.map((x) => `<option value="${x.id}">${x.name}</option>`).join("");
    el.bindingServiceId.innerHTML = opts || '<option value="">No services</option>';
    el.depParentId.innerHTML = opts || '<option value="">No services</option>';
    el.depChildId.innerHTML = opts || '<option value="">No services</option>';
  }

  function renderKpis(summary) {
    el.kpiServices.textContent = String(summary?.total_services || 0);
    el.kpiImpacted.textContent = String((summary?.impacted || 0) + (summary?.degraded || 0));
    el.kpiDown.textContent = String(summary?.down || 0);
  }

  function renderGraph(app, topology, health) {
    if (!window.cytoscape) {
      showMsg("Graph library failed to load", "danger");
      return;
    }

    const serviceHealth = {};
    (health.services || []).forEach((row) => {
      serviceHealth[row.service.id] = row;
    });

    const elements = [];
    elements.push({
      data: {
        id: `app-${app.id}`,
        label: app.name,
        kind: "application",
        health: health.application_health || "UP",
      },
    });

    (topology.services || []).forEach((s) => {
      const row = serviceHealth[s.id];
      const st = row?.health || "UP";
      elements.push({
        classes: `health-${String(st).toLowerCase()}`,
        data: {
          id: `svc-${s.id}`,
          label: s.name,
          kind: "service",
          health: st,
          serviceId: s.id,
        },
      });
      elements.push({
        data: {
          id: `e-app-${app.id}-${s.id}`,
          source: `app-${app.id}`,
          target: `svc-${s.id}`,
          kind: "membership",
        },
      });
    });

    (topology.dependencies || []).forEach((d) => {
      elements.push({
        data: {
          id: `dep-${d.id}`,
          source: `svc-${d.parent_service_id}`,
          target: `svc-${d.child_service_id}`,
          kind: "dependency",
          depType: d.dependency_type,
          lineStyle: d.dependency_type === "soft" ? "dashed" : "solid",
        },
      });
    });

    const serviceIds = new Set((topology.services || []).map((x) => x.id));
    const existingDepKeys = new Set(
      (topology.dependencies || []).map((d) => `${d.parent_service_id}->${d.child_service_id}`)
    );
    (state.itamDependencySuggestions || []).forEach((d, idx) => {
      const parentId = Number(d.parent_service_id || 0);
      const childId = Number(d.child_service_id || 0);
      if (!parentId || !childId || parentId === childId) return;
      if (!serviceIds.has(parentId) || !serviceIds.has(childId)) return;
      const depKey = `${parentId}->${childId}`;
      if (existingDepKeys.has(depKey)) return;

      elements.push({
        data: {
          id: `itam-dep-${parentId}-${childId}-${idx}`,
          source: `svc-${parentId}`,
          target: `svc-${childId}`,
          kind: "itam-dependency",
          depType: "itam_suggested",
          confidence: Number(d.confidence || 0),
        },
      });
    });

    (topology.bindings || []).forEach((b) => {
      const id = `bind-${b.id}`;
      const label = `${b.monitor_type}:${b.monitor_ref}`;
      elements.push({
        data: {
          id,
          label,
          kind: "binding",
          bindingId: b.id,
        },
      });
      elements.push({
        data: {
          id: `e-bind-${b.id}`,
          source: `svc-${b.service_id}`,
          target: id,
          kind: "binding-link",
        },
      });
    });

    if (state.cy) {
      state.cy.destroy();
      state.cy = null;
    }

    state.cy = cytoscape({
      container: el.graph,
      elements,
      style: [
        {
          selector: "node[kind='application']",
          style: {
            shape: "round-rectangle",
            "background-color": cyColor(health.application_health || "UP"),
            color: "#ffffff",
            label: "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "font-size": 12,
            "font-weight": 700,
            width: 190,
            height: 60,
            "border-width": 2,
            "border-color": "#0d2e4f",
          },
        },
        {
          selector: "node[kind='service']",
          style: {
            shape: "round-rectangle",
            "background-color": "#26b96f",
            color: "#ffffff",
            label: "data(label)",
            "text-wrap": "wrap",
            "text-max-width": 130,
            "text-valign": "center",
            "text-halign": "center",
            "font-size": 11,
            width: 150,
            height: 52,
            "border-width": 2,
            "border-color": "#1f3653",
          },
        },
        {
          selector: "node.health-down",
          style: { "background-color": "#df4d52" },
        },
        {
          selector: "node.health-impacted",
          style: { "background-color": "#f2b64a", color: "#283444" },
        },
        {
          selector: "node.health-degraded",
          style: { "background-color": "#4ea0f4" },
        },
        {
          selector: "node.health-up",
          style: { "background-color": "#26b96f" },
        },
        {
          selector: "node[kind='binding']",
          style: {
            shape: "ellipse",
            "background-color": "#7f8ea3",
            color: "#11243a",
            label: "data(label)",
            "text-wrap": "wrap",
            "text-max-width": 110,
            "font-size": 9,
            width: 36,
            height: 36,
            opacity: 0.9,
          },
        },
        {
          selector: "edge[kind='membership']",
          style: {
            width: 1.5,
            "line-color": "#9db2c7",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#9db2c7",
            "curve-style": "bezier",
          },
        },
        {
          selector: "edge[kind='dependency']",
          style: {
            width: 2.4,
            "line-color": "#5f6b78",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#5f6b78",
            "curve-style": "bezier",
            "line-style": "data(lineStyle)",
          },
        },
        {
          selector: "edge[kind='itam-dependency']",
          style: {
            width: 2,
            "line-color": "#d89a2f",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#d89a2f",
            "curve-style": "bezier",
            "line-style": "dashed",
            opacity: 0.95,
          },
        },
        {
          selector: "edge[kind='binding-link']",
          style: {
            width: 1.2,
            "line-color": "#aab7c4",
            "target-arrow-shape": "none",
            "curve-style": "bezier",
            "line-style": "dashed",
          },
        },
        {
          selector: ":selected",
          style: {
            "border-width": 4,
            "border-color": "#111111",
            "line-color": "#111111",
            "target-arrow-color": "#111111",
          },
        },
      ],
      layout: {
        name: "breadthfirst",
        directed: true,
        roots: [`app-${app.id}`],
        spacingFactor: 1.3,
        padding: 18,
      },
    });

    const saved = (state.sharedLayout && Object.keys(state.sharedLayout).length
      ? state.sharedLayout
      : loadSavedLayout(app.id));
    if (saved) {
      state.cy.nodes().forEach((n) => {
        const p = saved[n.id()];
        if (p && typeof p.x === "number" && typeof p.y === "number") {
          n.position({ x: p.x, y: p.y });
        }
      });
      state.cy.fit(state.cy.elements(":visible"), 24);
    }

    state.cy.on("tap", "node", (evt) => {
      const d = evt.target.data();
      if (d.kind === "service") {
        state.selectedServiceId = d.serviceId;
        const row = (health.services || []).find((x) => x.service.id === d.serviceId);
        renderImpactDetails(row || null);
      } else if (d.kind === "binding") {
        renderImpactDetails({
          service: { name: d.label, service_type: "binding", criticality: "-" },
          health: "INFO",
          reasons: [],
          affected_services: [],
        });
      } else if (d.kind === "application") {
        renderImpactDetails(null);
      }
    });

    state.cy.on("dragfree", "node", () => {
      saveCurrentLayout(app.id);
    });

    applyGraphFilters();
  }

  function dependencyAdjacency(deps) {
    const byParent = {};
    const byChild = {};
    (deps || []).forEach((d) => {
      if (!byParent[d.parent_service_id]) byParent[d.parent_service_id] = [];
      byParent[d.parent_service_id].push(d.child_service_id);
      if (!byChild[d.child_service_id]) byChild[d.child_service_id] = [];
      byChild[d.child_service_id].push(d.parent_service_id);
    });
    return { byParent, byChild };
  }

  function collectUpstream(startIds, byChild) {
    const seen = new Set(startIds);
    const q = [...startIds];
    while (q.length) {
      const curr = q.shift();
      const parents = byChild[curr] || [];
      parents.forEach((p) => {
        if (!seen.has(p)) {
          seen.add(p);
          q.push(p);
        }
      });
    }
    return seen;
  }

  function collectDownstream(startIds, byParent) {
    const seen = new Set(startIds);
    const q = [...startIds];
    while (q.length) {
      const curr = q.shift();
      const children = byParent[curr] || [];
      children.forEach((c) => {
        if (!seen.has(c)) {
          seen.add(c);
          q.push(c);
        }
      });
    }
    return seen;
  }

  function computeVisibleServiceIds() {
    const dash = state.dashboard;
    if (!dash) return new Set();
    const mode = el.graphFilter?.value || "all";
    const services = dash.topology?.services || [];
    const healthRows = dash.health?.services || [];
    const h = Object.fromEntries(healthRows.map((x) => [x.service.id, x.health]));
    const deps = [...(dash.topology?.dependencies || [])];
    const serviceIds = new Set(services.map((s) => s.id));
    const depKeys = new Set(deps.map((d) => `${d.parent_service_id}->${d.child_service_id}`));
    (state.itamDependencySuggestions || []).forEach((d) => {
      const parentId = Number(d.parent_service_id || 0);
      const childId = Number(d.child_service_id || 0);
      if (!parentId || !childId || parentId === childId) return;
      if (!serviceIds.has(parentId) || !serviceIds.has(childId)) return;
      const key = `${parentId}->${childId}`;
      if (depKeys.has(key)) return;
      depKeys.add(key);
      deps.push({
        parent_service_id: parentId,
        child_service_id: childId,
      });
    });
    const { byParent, byChild } = dependencyAdjacency(deps);

    const all = new Set(services.map((s) => s.id));
    if (mode === "all") return all;

    const down = services.filter((s) => h[s.id] === "DOWN").map((s) => s.id);
    const attention = services
      .filter((s) => ["DOWN", "IMPACTED", "DEGRADED"].includes(h[s.id]))
      .map((s) => s.id);

    if (mode === "down_only") {
      return collectUpstream(down, byChild);
    }
    if (mode === "attention") {
      const base = new Set(attention);
      collectUpstream(attention, byChild).forEach((x) => base.add(x));
      collectDownstream(attention, byParent).forEach((x) => base.add(x));
      return base;
    }
    if (mode === "critical_path") {
      const fromDown = collectUpstream(down, byChild);
      const critical = services
        .filter((s) => (s.criticality || "").toLowerCase() === "critical")
        .map((s) => s.id);
      critical.forEach((x) => fromDown.add(x));
      return fromDown;
    }
    return all;
  }

  function applyGraphFilters() {
    if (!state.cy || !state.dashboard) return;
    const visibleSvc = computeVisibleServiceIds();
    const showBindings = !!el.toggleBindings?.checked;

    state.cy.nodes().forEach((n) => {
      const d = n.data();
      let visible = true;
      if (d.kind === "service") {
        visible = visibleSvc.has(d.serviceId);
      } else if (d.kind === "binding") {
        visible = showBindings;
      }
      n.style("display", visible ? "element" : "none");
    });

    state.cy.edges().forEach((e) => {
      const d = e.data();
      let visible = true;
      if (d.kind === "dependency") {
        const src = e.source().data();
        const dst = e.target().data();
        visible = visibleSvc.has(src.serviceId) && visibleSvc.has(dst.serviceId);
      } else if (d.kind === "itam-dependency") {
        const src = e.source().data();
        const dst = e.target().data();
        visible = visibleSvc.has(src.serviceId) && visibleSvc.has(dst.serviceId);
      } else if (d.kind === "membership") {
        const dst = e.target().data();
        visible = dst.kind !== "service" || visibleSvc.has(dst.serviceId);
      } else if (d.kind === "binding-link") {
        visible = showBindings;
      }
      e.style("display", visible ? "element" : "none");
    });

    state.cy.fit(state.cy.elements(":visible"), 24);
  }

  function renderTables(app, topology, health) {
    const services = topology.services || [];
    const bindings = topology.bindings || [];
    const deps = [...(topology.dependencies || [])];
    const serviceIds = new Set(services.map((s) => s.id));
    const depKeys = new Set(deps.map((d) => `${d.parent_service_id}->${d.child_service_id}`));
    (state.itamDependencySuggestions || []).forEach((d, idx) => {
      const parentId = Number(d.parent_service_id || 0);
      const childId = Number(d.child_service_id || 0);
      if (!parentId || !childId || parentId === childId) return;
      if (!serviceIds.has(parentId) || !serviceIds.has(childId)) return;
      const key = `${parentId}->${childId}`;
      if (depKeys.has(key)) return;
      depKeys.add(key);
      deps.push({
        id: `itam-${idx}-${parentId}-${childId}`,
        parent_service_id: parentId,
        child_service_id: childId,
        dependency_type: `itam_suggested (${Number(d.confidence || 0)})`,
        _inferred: true,
      });
    });
    const serviceById = Object.fromEntries(services.map((x) => [x.id, x]));
    const healthByService = Object.fromEntries((health.services || []).map((x) => [x.service.id, x]));

    fillServiceDropdowns(services);

    el.servicesTbody.innerHTML = "";
    if (!services.length) {
      el.servicesTbody.innerHTML = '<tr><td colspan="5" class="text-muted text-center py-3">No services</td></tr>';
    } else {
      services.forEach((svc) => {
        const row = healthByService[svc.id];
        const status = row?.health || "UP";
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>
            <div class="fw-semibold">${svc.name}</div>
            <div class="small text-muted">${(row?.affected_services || []).map((x) => x.service_name).join(", ")}</div>
          </td>
          <td>${svc.service_type || "-"}</td>
          <td>${svc.criticality || "-"}</td>
          <td><span class="badge ${statusClass(status)}">${status}</span></td>
          <td>
            <button class="btn btn-sm btn-danger del-service" data-id="${svc.id}">
              <i data-lucide="trash-2" width="14"></i>
            </button>
          </td>
        `;
        el.servicesTbody.appendChild(tr);
      });
    }

    el.bindingsTbody.innerHTML = "";
    if (!bindings.length) {
      el.bindingsTbody.innerHTML = '<tr><td colspan="3" class="text-muted text-center py-3">No bindings</td></tr>';
    } else {
      bindings.forEach((b) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${serviceById[b.service_id]?.name || b.service_id}</td>
          <td><code>${b.monitor_type}:${b.monitor_ref}</code></td>
          <td>
            <button class="btn btn-sm btn-danger del-binding" data-id="${b.id}">
              <i data-lucide="trash-2" width="14"></i>
            </button>
          </td>
        `;
        el.bindingsTbody.appendChild(tr);
      });
    }

    el.depsTbody.innerHTML = "";
    if (!deps.length) {
      el.depsTbody.innerHTML = '<tr><td colspan="4" class="text-muted text-center py-3">No dependencies</td></tr>';
    } else {
      deps.forEach((d) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${serviceById[d.parent_service_id]?.name || d.parent_service_id}</td>
          <td>${serviceById[d.child_service_id]?.name || d.child_service_id}</td>
          <td>${d.dependency_type}</td>
          <td>
            ${d._inferred ? '<span class="text-muted">suggested</span>' : `
              <button class="btn btn-sm btn-danger del-dep" data-id="${d.id}">
                <i data-lucide="trash-2" width="14"></i>
              </button>
            `}
          </td>
        `;
        el.depsTbody.appendChild(tr);
      });
    }

    if (window.lucide) lucide.createIcons();
  }

  function renderNoSelection() {
    el.selectedAppTitle.textContent = "Select an application";
    el.selectedAppMeta.textContent = "No application selected.";
    el.appHealthBadge.className = "badge text-bg-secondary";
    el.appHealthBadge.textContent = "N/A";
    el.openServiceModalBtn.disabled = true;
    el.openBindingModalBtn.disabled = true;
    el.openDepModalBtn.disabled = true;
    if (el.runBindingQualityBtn) el.runBindingQualityBtn.disabled = true;
    if (el.runBindingSuggestBtn) el.runBindingSuggestBtn.disabled = true;
    if (el.runItamDepSuggestBtn) el.runItamDepSuggestBtn.disabled = true;
    if (el.applyItamDepSuggestBtn) el.applyItamDepSuggestBtn.disabled = true;
    el.servicesTbody.innerHTML = '<tr><td colspan="5" class="text-muted text-center py-3">No services</td></tr>';
    el.bindingsTbody.innerHTML = '<tr><td colspan="3" class="text-muted text-center py-3">No bindings</td></tr>';
    el.depsTbody.innerHTML = '<tr><td colspan="4" class="text-muted text-center py-3">No dependencies</td></tr>';
    if (el.bindingQualitySummary) el.bindingQualitySummary.textContent = "Run Quality to detect stale bindings.";
    if (el.bindingQualityList) el.bindingQualityList.innerHTML = "";
    if (el.bindingSuggestionList) el.bindingSuggestionList.innerHTML = "";
    if (el.itamDepSuggestSummary) el.itamDepSuggestSummary.textContent = "Preview ITAM-derived service dependencies.";
    if (el.itamDepSuggestList) el.itamDepSuggestList.innerHTML = "";
    state.itamDependencySuggestions = [];
    state.sharedLayout = null;
    renderImpactDetails(null);
    renderKpis(null);
    if (state.cy) {
      state.cy.destroy();
      state.cy = null;
    }
    el.graph.innerHTML = "";
  }

  async function loadSelectedDashboard() {
    if (!state.selectedAppId) {
      state.dashboard = null;
      renderNoSelection();
      return;
    }

    const data = await api(`/api/itom/applications/${state.selectedAppId}/dashboard`);
    state.dashboard = data;
    try {
      state.sharedLayout = await loadSharedLayout(state.selectedAppId);
    } catch (_) {
      state.sharedLayout = null;
    }

    const app = data.application;
    const topology = data.topology || { services: [], bindings: [], dependencies: [] };
    const health = data.health || { application_health: "UP", summary: {}, services: [] };

    el.selectedAppTitle.textContent = app.name;
    el.selectedAppMeta.textContent = `Owner: ${app.owner || "-"} | Tier: ${app.tier || "-"} | Customer: ${app.customer_name || "-"}`;
    el.appHealthBadge.className = `badge ${statusClass(health.application_health)}`;
    el.appHealthBadge.textContent = health.application_health || "UP";

    el.openServiceModalBtn.disabled = false;
    el.openBindingModalBtn.disabled = false;
    el.openDepModalBtn.disabled = false;
    if (el.runBindingQualityBtn) el.runBindingQualityBtn.disabled = false;
    if (el.runBindingSuggestBtn) el.runBindingSuggestBtn.disabled = false;
    if (el.runItamDepSuggestBtn) el.runItamDepSuggestBtn.disabled = false;
    if (el.applyItamDepSuggestBtn) el.applyItamDepSuggestBtn.disabled = false;

    try {
      await loadItamDependencySuggestions(true);
    } catch (err) {
      state.itamDependencySuggestions = [];
      if (el.itamDepSuggestSummary) {
        el.itamDepSuggestSummary.textContent = err.message || "Failed to load ITAM dependency suggestions.";
      }
      if (el.itamDepSuggestList) {
        el.itamDepSuggestList.innerHTML = "";
      }
    }

    renderKpis(health.summary);
    renderTables(app, topology, health);
    renderGraph(app, topology, health);

    if (state.selectedServiceId) {
      const row = (health.services || []).find((x) => x.service.id === state.selectedServiceId);
      renderImpactDetails(row || null);
    } else {
      renderImpactDetails(null);
    }
  }

  function renderBindingQuality(data) {
    if (!el.bindingQualitySummary || !el.bindingQualityList) return;
    const s = data?.summary || {};
    el.bindingQualitySummary.textContent =
      `Total ${s.total_bindings || 0} | Valid ${s.valid_bindings || 0} | Stale ${s.stale_bindings || 0} | Duplicate Keys ${s.duplicate_keys || 0}`;

    const stale = data?.stale_bindings || [];
    if (!stale.length) {
      el.bindingQualityList.innerHTML = '<span class="text-success">No stale bindings detected.</span>';
      return;
    }
    el.bindingQualityList.innerHTML = stale
      .slice(0, 20)
      .map(
        (x) =>
          `<div class="itom-badge-line danger">[${x.reason}] ${x.service_name || x.service_id} -> ${x.monitor_type}:${x.monitor_ref}</div>`
      )
      .join("");
  }

  function renderBindingSuggestions(data) {
    if (!el.bindingSuggestionList) return;
    const items = data?.items || [];
    if (!items.length) {
      el.bindingSuggestionList.innerHTML = '<span class="text-muted">No actionable suggestions from active alerts.</span>';
      return;
    }

    el.bindingSuggestionList.innerHTML = items
      .slice(0, 20)
      .map((x, idx) => {
        const recos = x.recommended_services || [];
        const options = recos
          .map((r) => `<option value="${r.service_id}">${r.service_name} (score ${r.score})</option>`)
          .join("");
        return `
          <div class="itom-suggestion mb-2 p-2 border rounded">
            <div class="fw-semibold">${x.monitor_type}:${x.monitor_ref}</div>
            <div class="small text-muted mb-2">${x.source_kind}${x.rule_name ? ` | ${x.rule_name}` : ""}</div>
            <div class="d-flex gap-2 align-items-center">
              <select class="form-select form-select-sm suggestion-service" data-idx="${idx}">
                ${options || '<option value="">Select service</option>'}
              </select>
              <button class="btn btn-sm btn-primary apply-suggestion"
                      data-monitor-type="${x.monitor_type}"
                      data-monitor-ref="${x.monitor_ref}"
                      data-display-name="${(x.display_name || "").replace(/"/g, "&quot;")}">
                Apply
              </button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderItamDependencySuggestions(data) {
    if (!el.itamDepSuggestSummary || !el.itamDepSuggestList) return;
    const total = Number(data?.total_suggestions || 0);
    const fresh = Number(data?.new_dependency_suggestions || 0);
    el.itamDepSuggestSummary.textContent = `${fresh} new / ${total} total dependency suggestions`;

    const items = (data?.items || []).slice(0, 20);
    if (!items.length) {
      el.itamDepSuggestList.innerHTML = '<span class="text-muted">No ITAM dependency suggestions.</span>';
      return;
    }

    el.itamDepSuggestList.innerHTML = items
      .map((x) => {
        const status = x.already_exists ? "exists" : "new";
        return `
          <div class="itom-badge-line ${x.already_exists ? "ok" : "danger"}">
            ${x.parent_service_name || x.parent_service_id} -> ${x.child_service_name || x.child_service_id}
            | conf ${x.confidence} | ${status}
          </div>
        `;
      })
      .join("");
  }

  async function loadBindingQuality() {
    if (!state.selectedAppId) return;
    const data = await api(`/api/itom/applications/${state.selectedAppId}/binding-quality`);
    renderBindingQuality(data);
  }

  async function loadBindingSuggestions() {
    if (!state.selectedAppId) return;
    const data = await api(`/api/itom/applications/${state.selectedAppId}/binding-suggestions`);
    renderBindingSuggestions(data);
  }

  async function loadItamDependencySuggestions(render = true) {
    const params = new URLSearchParams();
    params.set("min_confidence", "70");
    params.set("limit", "300");
    if (state.selectedCustomerId) {
      params.set("customer_id", String(state.selectedCustomerId));
    }
    const data = await api(`/api/itam/itom/dependency-suggestions?${params.toString()}`);
    state.itamDependencySuggestions = data?.items || [];
    if (render) {
      renderItamDependencySuggestions(data);
    }
    return data;
  }

  async function applyItamDependencySuggestions() {
    const payload = {
      min_confidence: 70,
      limit: 500,
    };
    if (state.selectedCustomerId) {
      payload.customer_id = Number(state.selectedCustomerId);
    }
    return api("/api/itam/itom/dependency-suggestions/apply", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }

  async function loadBindingOptions() {
    const appId = state.selectedAppId;
    const monitorType = (el.bindingMonitorType.value || "").trim();
    if (!appId || !monitorType) {
      el.bindingMonitorRef.innerHTML = '<option value="">Select monitor type</option>';
      return;
    }

    const data = await api(`/api/itom/monitor-options?app_id=${appId}&monitor_type=${encodeURIComponent(monitorType)}`);
    const items = data.items || [];
    if (!items.length) {
      el.bindingMonitorRef.innerHTML = '<option value="">No monitor references available</option>';
      return;
    }
    el.bindingMonitorRef.innerHTML = items
      .map((x) => `<option value="${x.value}" data-label="${x.label.replace(/"/g, "&quot;")}">${x.label}</option>`)
      .join("");
  }

  el.customerFilter.addEventListener("change", async () => {
    state.selectedCustomerId = el.customerFilter.value;
    el.appCustomerId.value = state.selectedCustomerId;
    state.selectedAppId = null;
    state.selectedServiceId = null;
    await loadApplications();
  });

  el.refreshBtn.addEventListener("click", async () => {
    await loadApplications();
    showMsg("ITOM dashboard refreshed");
  });

  el.graphFilter.addEventListener("change", () => {
    applyGraphFilters();
  });

  el.toggleBindings.addEventListener("change", () => {
    applyGraphFilters();
  });

  el.saveLayoutBtn.addEventListener("click", () => {
    (async () => {
      if (!state.selectedAppId) return;
      saveCurrentLayout(state.selectedAppId);
      try {
        await persistLayout(state.selectedAppId);
        showMsg("Layout saved for this application");
      } catch (err) {
        showMsg(err.message || "Layout save failed", "danger");
      }
    })();
  });

  el.resetLayoutBtn.addEventListener("click", () => {
    (async () => {
      if (!state.selectedAppId || !state.cy) return;
      resetSavedLayout(state.selectedAppId);
      try {
        await api(`/api/itom/applications/${state.selectedAppId}/layout`, { method: "DELETE" });
      } catch (_) {
        // allow local reset even if server delete fails
      }
      state.sharedLayout = null;
      state.cy.layout({
        name: "breadthfirst",
        directed: true,
        roots: [`app-${state.selectedAppId}`],
        spacingFactor: 1.3,
        padding: 18,
      }).run();
      showMsg("Layout reset");
    })();
  });

  el.openAppModalBtn.addEventListener("click", () => appModal.show());
  el.openServiceModalBtn.addEventListener("click", () => serviceModal.show());
  el.openBindingModalBtn.addEventListener("click", async () => {
    await loadBindingOptions();
    bindingModal.show();
  });
  el.openDepModalBtn.addEventListener("click", () => depModal.show());

  if (el.runBindingQualityBtn) {
    el.runBindingQualityBtn.addEventListener("click", async () => {
      try {
        await loadBindingQuality();
        showMsg("Binding quality scan completed");
      } catch (err) {
        showMsg(err.message, "danger");
      }
    });
  }

  if (el.runBindingSuggestBtn) {
    el.runBindingSuggestBtn.addEventListener("click", async () => {
      try {
        await loadBindingSuggestions();
        showMsg("Binding suggestions refreshed");
      } catch (err) {
        showMsg(err.message, "danger");
      }
    });
  }

  if (el.runItamDepSuggestBtn) {
    el.runItamDepSuggestBtn.addEventListener("click", async () => {
      try {
        await loadItamDependencySuggestions();
        if (state.dashboard) {
          const app = state.dashboard.application;
          const topology = state.dashboard.topology || { services: [], bindings: [], dependencies: [] };
          const health = state.dashboard.health || { application_health: "UP", summary: {}, services: [] };
          renderTables(app, topology, health);
          renderGraph(app, topology, health);
        }
        showMsg("ITAM dependency suggestions refreshed");
      } catch (err) {
        showMsg(err.message, "danger");
      }
    });
  }

  if (el.applyItamDepSuggestBtn) {
    el.applyItamDepSuggestBtn.addEventListener("click", async () => {
      try {
        const res = await applyItamDependencySuggestions();
        await loadSelectedDashboard();
        showMsg(`Applied ${res.created || 0} ITAM dependency suggestions`);
      } catch (err) {
        showMsg(err.message, "danger");
      }
    });
  }

  el.bindingMonitorType.addEventListener("change", async () => {
    try {
      await loadBindingOptions();
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  el.appsTbody.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (!id) return;

    if (btn.classList.contains("select-app")) {
      state.selectedAppId = id;
      state.selectedServiceId = null;
      renderApplications();
      await loadSelectedDashboard();
      return;
    }

    if (btn.classList.contains("del-app")) {
      if (!window.confirm("Delete this application and all services/bindings/dependencies?")) return;
      try {
        await api(`/api/itom/applications/${id}`, { method: "DELETE" });
        if (state.selectedAppId === id) {
          state.selectedAppId = null;
          state.selectedServiceId = null;
        }
        await loadApplications();
        showMsg("Application deleted");
      } catch (err) {
        showMsg(err.message, "danger");
      }
    }
  });

  el.servicesTbody.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button.del-service");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (!id) return;
    if (!window.confirm("Delete this service?")) return;
    try {
      await api(`/api/itom/services/${id}`, { method: "DELETE" });
      await loadSelectedDashboard();
      showMsg("Service deleted");
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  el.bindingsTbody.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button.del-binding");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (!id) return;
    if (!window.confirm("Delete this binding?")) return;
    try {
      await api(`/api/itom/bindings/${id}`, { method: "DELETE" });
      await loadSelectedDashboard();
      showMsg("Binding deleted");
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  el.depsTbody.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button.del-dep");
    if (!btn) return;
    const id = Number(btn.dataset.id);
    if (!id) return;
    if (!window.confirm("Delete this dependency?")) return;
    try {
      await api(`/api/itom/dependencies/${id}`, { method: "DELETE" });
      await loadSelectedDashboard();
      showMsg("Dependency deleted");
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  if (el.bindingSuggestionList) {
    el.bindingSuggestionList.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("button.apply-suggestion");
      if (!btn || !state.selectedAppId) return;

      const wrap = btn.closest(".itom-suggestion");
      const sel = wrap ? wrap.querySelector("select.suggestion-service") : null;
      const serviceId = sel ? Number(sel.value) : 0;
      if (!serviceId) {
        showMsg("Select a target service first", "danger");
        return;
      }

      try {
        await api(`/api/itom/applications/${state.selectedAppId}/binding-suggestions/apply`, {
          method: "POST",
          body: JSON.stringify({
            service_id: serviceId,
            monitor_type: btn.dataset.monitorType,
            monitor_ref: btn.dataset.monitorRef,
            display_name: btn.dataset.displayName || "",
          }),
        });
        await loadSelectedDashboard();
        await loadBindingQuality();
        await loadBindingSuggestions();
        showMsg("Suggestion applied");
      } catch (err) {
        showMsg(err.message, "danger");
      }
    });
  }

  el.appForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const payload = {
        customer_id: Number(document.getElementById("appCustomerId").value),
        name: document.getElementById("appName").value.trim(),
        code: document.getElementById("appCode").value.trim(),
        owner: document.getElementById("appOwner").value.trim(),
        tier: document.getElementById("appTier").value.trim(),
        description: document.getElementById("appDescription").value.trim(),
      };
      const res = await api("/api/itom/applications", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.selectedCustomerId = String(res.item.customer_id);
      state.selectedAppId = res.item.id;
      state.selectedServiceId = null;
      appModal.hide();
      ev.target.reset();
      await loadApplications();
      showMsg("Application created");
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  el.serviceForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.selectedAppId) return;
    try {
      const payload = {
        application_id: state.selectedAppId,
        name: document.getElementById("serviceName").value.trim(),
        service_type: document.getElementById("serviceType").value.trim(),
        criticality: document.getElementById("serviceCriticality").value.trim(),
        runbook_url: document.getElementById("serviceRunbook").value.trim(),
      };
      await api("/api/itom/services", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      serviceModal.hide();
      ev.target.reset();
      await loadSelectedDashboard();
      showMsg("Service added");
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  el.bindingForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const serviceId = Number(el.bindingServiceId.value);
      const option = el.bindingMonitorRef.options[el.bindingMonitorRef.selectedIndex];
      const monitorRef = el.bindingMonitorRef.value;
      if (!monitorRef) {
        showMsg("Select a monitor reference", "danger");
        return;
      }
      const payload = {
        monitor_type: el.bindingMonitorType.value.trim(),
        monitor_ref: monitorRef,
        display_name: option ? option.dataset.label || option.textContent : monitorRef,
      };
      await api(`/api/itom/services/${serviceId}/bindings`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      bindingModal.hide();
      ev.target.reset();
      await loadSelectedDashboard();
      showMsg("Binding added");
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  el.depForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const payload = {
        parent_service_id: Number(el.depParentId.value),
        child_service_id: Number(el.depChildId.value),
        dependency_type: document.getElementById("depType").value.trim(),
      };
      await api("/api/itom/dependencies", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      depModal.hide();
      ev.target.reset();
      await loadSelectedDashboard();
      showMsg("Dependency added");
    } catch (err) {
      showMsg(err.message, "danger");
    }
  });

  async function init() {
    try {
      await loadCustomers();
      await loadMonitorTypes();
      await loadApplications();
      if (window.lucide) lucide.createIcons();
    } catch (err) {
      showMsg(err.message, "danger");
    }
  }

  init();
});
