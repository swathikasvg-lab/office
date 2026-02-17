document.addEventListener("DOMContentLoaded", () => {
  const launcher = document.getElementById("copilotLauncher");
  const panelEl = document.getElementById("copilotPanel");
  const panel = panelEl ? bootstrap.Offcanvas.getOrCreateInstance(panelEl) : null;
  const messages = document.getElementById("copilotMessages");
  const form = document.getElementById("copilotForm");
  const input = document.getElementById("copilotInput");
  const suggestionsEl = document.getElementById("copilotSuggestions");

  if (!launcher || !panel || !messages || !form || !input || !suggestionsEl) {
    return;
  }

  function scrollBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function msgHtml(role, text, meta = "") {
    return `
      <div class="copilot-msg ${role}">
        <div>${text}</div>
        ${meta ? `<div class="meta">${meta}</div>` : ""}
      </div>
    `;
  }

  function addUser(text) {
    messages.insertAdjacentHTML("beforeend", msgHtml("user", text));
    scrollBottom();
  }

  function addBot(text, meta = "") {
    messages.insertAdjacentHTML("beforeend", msgHtml("bot", text, meta));
    scrollBottom();
  }

  function addUiActions(actions) {
    if (!Array.isArray(actions) || !actions.length) return;
    const html = `
      <div class="copilot-msg bot">
        <div><strong>Quick Actions</strong></div>
        <div class="mt-2 d-flex gap-2 flex-wrap">
          ${actions
            .map(
              (a) =>
                `<button class="btn btn-sm btn-outline-primary copilot-open-action" data-url="${String(
                  a.url || ""
                ).replace(/"/g, "&quot;")}">${a.label || "Open"}</button>`
            )
            .join("")}
        </div>
      </div>
    `;
    messages.insertAdjacentHTML("beforeend", html);
    const last = messages.lastElementChild;
    last.querySelectorAll(".copilot-open-action").forEach((btn) => {
      btn.addEventListener("click", () => {
        const url = btn.getAttribute("data-url");
        if (!url) return;
        window.location.href = url;
      });
    });
    scrollBottom();
  }

  function addReportIntentCard(intent) {
    if (!intent) return;
    const missing = Array.isArray(intent.missing_fields) ? intent.missing_fields : [];
    const missingText = missing.length ? `Missing: ${missing.join(", ")}` : "Ready to run";
    const canRun = !missing.length;
    const html = `
      <div class="copilot-msg bot">
        <div><strong>Report Plan</strong></div>
        <div>${intent.report_name} (ID ${intent.report_id}) | ${intent.from} -> ${intent.to} | ${intent.format}</div>
        <div class="meta">${missingText}</div>
        <div class="mt-2 d-flex gap-2">
          <button class="btn btn-sm btn-outline-primary copilot-open-report">Open Reports</button>
          <button class="btn btn-sm btn-outline-secondary copilot-copy-report">Copy Params</button>
          <button class="btn btn-sm btn-success copilot-run-report" ${canRun ? "" : "disabled"}>Run Report Now</button>
        </div>
      </div>
    `;
    messages.insertAdjacentHTML("beforeend", html);
    const last = messages.lastElementChild;
    const openBtn = last.querySelector(".copilot-open-report");
    const copyBtn = last.querySelector(".copilot-copy-report");
    const runBtn = last.querySelector(".copilot-run-report");
    if (openBtn) {
      openBtn.addEventListener("click", () => {
        window.location.href = intent.open_url || "/report_config";
      });
    }
    if (copyBtn) {
      copyBtn.addEventListener("click", async () => {
        const payload = {
          report_id: intent.report_id,
          from: intent.from,
          to: intent.to,
          format: intent.format,
          ...intent.params,
        };
        try {
          await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
          addBot("Report parameters copied to clipboard.");
        } catch (_) {
          addBot("Could not copy report parameters.");
        }
      });
    }
    if (runBtn) {
      runBtn.addEventListener("click", () => {
        if (!canRun) {
          addBot("Report cannot run yet. Provide missing fields first.");
          return;
        }

        const form = document.createElement("form");
        form.method = "POST";
        form.action = "/api/copilot/report/run";
        form.target = "_blank";
        form.style.display = "none";

        const fields = {
          report_id: intent.report_id,
          from: intent.from,
          to: intent.to,
          format: intent.format,
        };

        const p = intent.params || {};
        if (p.instance) fields.instance = p.instance;
        if (p.customer) fields.customer = p.customer;
        if (p.template_type) fields.template_type = p.template_type;
        if (p.device_name) fields.device_name = p.device_name;

        Object.entries(fields).forEach(([k, v]) => {
          if (v === undefined || v === null || String(v).trim() === "") return;
          const input = document.createElement("input");
          input.type = "hidden";
          input.name = k;
          input.value = String(v);
          form.appendChild(input);
        });

        document.body.appendChild(form);
        form.submit();
        form.remove();
        addBot("Report execution submitted. Check the new tab/download.");
      });
    }
    scrollBottom();
  }

  function formatEvidence(evidence) {
    if (!Array.isArray(evidence) || !evidence.length) return "";
    const tools = evidence.map((e) => e.tool).filter(Boolean);
    if (!tools.length) return "";
    return `tools: ${tools.join(", ")}`;
  }

  function formatActions(actions) {
    if (!Array.isArray(actions) || !actions.length) return "";
    return "next: " + actions.join(" | ");
  }

  async function ask(query) {
    addUser(query);
    input.value = "";

    try {
      const res = await fetch("/api/copilot/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) {
        addBot(data.error || "Copilot request failed.");
        return;
      }

      const metaParts = [];
      const ev = formatEvidence(data.evidence);
      const act = formatActions(data.actions);
      if (ev) metaParts.push(ev);
      if (act) metaParts.push(act);

      addBot(data.answer || "No answer generated.", metaParts.join(" | "));
      if (data.ui_actions) addUiActions(data.ui_actions);
      if (data.report_intent) addReportIntentCard(data.report_intent);
    } catch (err) {
      addBot("Copilot unavailable right now.");
    }
  }

  async function loadSuggestions() {
    try {
      const res = await fetch("/api/copilot/suggestions");
      const data = await res.json();
      if (!res.ok || data.ok === false || !Array.isArray(data.items)) return;

      suggestionsEl.innerHTML = "";
      data.items.slice(0, 7).forEach((text) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-outline-secondary btn-sm";
        b.textContent = text;
        b.addEventListener("click", () => ask(text));
        suggestionsEl.appendChild(b);
      });
    } catch (_) {
      // keep silent
    }
  }

  launcher.addEventListener("click", () => {
    panel.show();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = input.value.trim();
    if (!query) return;
    ask(query);
  });

  addBot("NOC Copilot ready. Ask for monitoring, ITOM impact, or ITAM inventory/risk/drift.");
  loadSuggestions();
});
