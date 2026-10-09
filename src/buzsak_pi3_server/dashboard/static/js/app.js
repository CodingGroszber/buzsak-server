// neugarden-server dashboard client (UI-04, UI-05, UI-06).
//
// Mostly read-only: this file polls GET /api/dashboard/state and never
// contacts a device directly (UI-05, UI-06). The one exception is the
// Sonoff MiniD "pulse" button (DEV-10), which POSTs to
// /api/dashboard/devices/<id>/pulse -- that endpoint only enqueues a
// request row; only the separate dispatcher process ever talks to the
// device (ARC-03). Kept as a single plain script deliberately: the JSON
// contract it renders is the one a future native client would consume too
// (see dashboard/__init__.py).

const POLL_INTERVAL_MS = 1000;
const STATE_URL = "/api/dashboard/state";

let lastState = null;
let selectedPartyId = null;

const healthPriority = { offline: 3, degraded: 2, unknown: 1, healthy: 0 };

function partyHealth(party) {
    if (!party.configured) {
        return { status: "not_configured", label: "not configured" };
    }
    if (party.devices.length === 0) {
        return { status: "unknown", label: "no devices" };
    }
    let worst = "healthy";
    for (const device of party.devices) {
        const status = device.health.status || "unknown";
        if ((healthPriority[status] ?? 1) > healthPriority[worst]) {
            worst = status;
        }
    }
    return { status: worst, label: worst.replace("_", " ") };
}

function renderHeader(state) {
    const list = document.getElementById("header-health");
    list.innerHTML = "";
    for (const party of state.parties) {
        const health = partyHealth(party);
        const item = document.createElement("li");
        item.className = "health-badge";
        item.dataset.status = health.status;
        item.innerHTML =
            '<span class="health-badge__dot" aria-hidden="true"></span>' +
            `<span>${party.label}: ${health.label}</span>`;
        list.appendChild(item);
    }
}

function renderTabs(state) {
    const nav = document.getElementById("tabs");
    nav.innerHTML = "";
    if (!selectedPartyId || !state.parties.some((p) => p.id === selectedPartyId)) {
        selectedPartyId = state.parties[0]?.id ?? null;
    }
    state.parties.forEach((party, index) => {
        const button = document.createElement("button");
        button.className = "tabs__button";
        button.type = "button";
        button.id = `tab-${party.id}`;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-selected", String(party.id === selectedPartyId));
        button.setAttribute("aria-controls", `panel-${party.id}`);
        button.tabIndex = party.id === selectedPartyId ? 0 : -1;
        button.textContent = party.label;
        button.addEventListener("click", () => {
            selectedPartyId = party.id;
            renderAll(lastState);
            document.getElementById(`tab-${party.id}`).focus();
        });
        button.addEventListener("keydown", (event) => {
            if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
            event.preventDefault();
            const delta = event.key === "ArrowRight" ? 1 : -1;
            const next = (index + delta + state.parties.length) % state.parties.length;
            selectedPartyId = state.parties[next].id;
            renderAll(lastState);
            document.getElementById(`tab-${selectedPartyId}`).focus();
        });
        nav.appendChild(button);
    });
}

function qualityPillClass(parameter) {
    if (parameter.stale) return "pill pill--stale";
    if (!parameter.has_data || parameter.quality !== "good") return "pill pill--invalid";
    return "pill pill--good";
}

function qualityLabel(parameter) {
    if (!parameter.has_data) return "no data";
    if (parameter.stale) return "stale";
    return parameter.quality;
}

function renderParameter(parameter) {
    const card = document.createElement("div");
    card.className = "kpi";
    const value = parameter.has_data && parameter.value !== null ? parameter.value : "—";
    card.innerHTML = `
    <div class="kpi__name">${parameter.id}</div>
    <div class="kpi__value">${value}${parameter.unit ? " " + parameter.unit : ""}</div>
    <div class="kpi__meta">
      <span class="${qualityPillClass(parameter)}">${qualityLabel(parameter)}</span>
      <span>${parameter.observed_at ?? "never observed"}</span>
    </div>`;
    return card;
}

const PULSE_IN_FLIGHT_STATUSES = new Set(["pending", "sent"]);

// One tap = one pulse, no confirmation step (garage-remote UX): the
// enqueue-side cooldown/health checks (DEV-10) are the safety net instead
// of a blocking popup.
function pulseStatusLabel(lastPulse) {
    if (!lastPulse) return null;
    if (lastPulse.error) return `Last attempt failed: ${lastPulse.error}`;
    if (lastPulse.status === "succeeded") return "Last attempt: opened OK";
    return `Last attempt: ${lastPulse.status}`;
}

function pulseStatusClass(lastPulse) {
    if (!lastPulse) return "device-card__pulse-status";
    if (lastPulse.error || lastPulse.status === "failed" || lastPulse.status === "expired") {
        return "device-card__pulse-status device-card__pulse-status--error";
    }
    if (lastPulse.status === "succeeded") {
        return "device-card__pulse-status device-card__pulse-status--ok";
    }
    return "device-card__pulse-status";
}

async function triggerPulse(deviceId, button) {
    button.disabled = true;
    button.dataset.state = "busy";
    button.textContent = "OPENING…";
    try {
        const response = await fetch(`/api/dashboard/devices/${encodeURIComponent(deviceId)}/pulse`, {
            method: "POST",
            headers: {
                Accept: "application/json",
                "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content ?? "",
            },
        });
        if (!response.ok) {
            button.dataset.state = "error";
            button.textContent = "REJECTED";
        }
    } catch (error) {
        button.dataset.state = "error";
        button.textContent = "OFFLINE";
    }
    // The next poll() refresh re-renders capabilities/last_pulse from fresh state.
}

function renderCapability(device, capability) {
    const button = document.createElement("button");
    button.type = "button";

    if (!capability.enabled || capability.action_id !== "pulse") {
        button.className = "action-button";
        button.textContent = capability.action_id;
        button.disabled = true;
        button.title = capability.disabled_reason || "The browser dashboard is read-only.";
        return button;
    }

    button.className = "action-button action-button--open";
    button.textContent = "OPEN";
    const inFlight = device.last_pulse && PULSE_IN_FLIGHT_STATUSES.has(device.last_pulse.status);
    button.disabled = Boolean(inFlight);
    button.dataset.state = inFlight ? "busy" : "ready";
    button.title = "Sends a single 0.5s pulse to open the garage door.";
    button.addEventListener("click", () => triggerPulse(device.id, button));
    return button;
}

function renderDevice(device) {
    const card = document.createElement("article");
    card.className = "device-card";

    const header = document.createElement("div");
    header.className = "device-card__header";
    header.innerHTML = `
    <span class="device-card__id">${device.label}${device.enabled ? "" : " (disabled)"}</span>
    <span class="device-card__address">${device.address}</span>`;
    card.appendChild(header);

    const grid = document.createElement("div");
    grid.className = "kpi-grid";
    for (const parameter of device.parameters) {
        grid.appendChild(renderParameter(parameter));
    }
    card.appendChild(grid);

    const actions = document.createElement("div");
    actions.className = "actions";
    if (device.capabilities.length === 0) {
        actions.innerHTML = '<span class="actions__empty">No control actions available yet.</span>';
    } else {
        for (const capability of device.capabilities) {
            actions.appendChild(renderCapability(device, capability));
        }
    }
    card.appendChild(actions);

    const pulseLabel = pulseStatusLabel(device.last_pulse);
    if (pulseLabel) {
        const status = document.createElement("div");
        status.className = pulseStatusClass(device.last_pulse);
        status.textContent = pulseLabel;
        card.appendChild(status);
    }

    return card;
}

function renderPanels(state) {
    const main = document.getElementById("tab-panels");
    main.innerHTML = "";
    for (const party of state.parties) {
        const panel = document.createElement("section");
        panel.id = `panel-${party.id}`;
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", `tab-${party.id}`);
        panel.hidden = party.id !== selectedPartyId;

        if (!party.configured) {
            const note = document.createElement("p");
            note.className = "party-note";
            note.textContent = party.note ?? "Not configured.";
            panel.appendChild(note);
        } else if (party.devices.length === 0) {
            const note = document.createElement("p");
            note.className = "party-note";
            note.textContent = "No devices registered yet.";
            panel.appendChild(note);
        } else {
            for (const device of party.devices) {
                panel.appendChild(renderDevice(device));
            }
        }
        main.appendChild(panel);
    }
}

function renderAll(state) {
    renderHeader(state);
    renderTabs(state);
    renderPanels(state);
}

function setConnectionStatus(connected, generatedAt) {
    const line = document.getElementById("status-line");
    line.dataset.connected = String(connected);
    line.textContent = connected
        ? `Last updated ${generatedAt}`
        : "Lost connection to the server — showing last known data.";
}

function renderServerHealth(status, label) {
    const badge = document.getElementById("server-health");
    badge.dataset.status = status;
    badge.querySelector("span:last-child").textContent = `server: ${label}`;
}

async function poll() {
    let response;
    try {
        response = await fetch(STATE_URL, { headers: { Accept: "application/json" } });
    } catch (error) {
        // Network/connection failure -- the server process itself may be down.
        setConnectionStatus(false);
        renderServerHealth("offline", "unreachable");
        return;
    }
    if (!response.ok) {
        // The process is responding but reports itself unavailable (e.g. DB unreachable).
        setConnectionStatus(false);
        renderServerHealth("degraded", "unavailable");
        return;
    }
    const payload = await response.json();
    lastState = payload;
    renderAll(payload);
    setConnectionStatus(true, payload.generated_at);
    renderServerHealth("healthy", "healthy");
}

poll();
setInterval(poll, POLL_INTERVAL_MS);
