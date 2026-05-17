/* MK7BoostGauge UI v2 — Socket.IO + REST + OBD2 tool + PCM live */

const $ = (id) => document.getElementById(id);
const socket = io();

// Only fields actually editable in the UI. Temp bounds, formula, dead zone
// bounds are hardcoded internally (consistent with reference project).
const CFG_FIELDS = ["map_min_mbar", "map_max_mbar", "scale", "offset_c", "tx_rate_hz"];

// Defaults if backend returns sparse config — keeps inputs filled.
const CFG_DEFAULTS = {
  map_min_mbar: 300, map_max_mbar: 2500,
  scale: 1.0, offset_c: 0, tx_rate_hz: 25,
};

let currentMode = "pcm";
let currentListenOnly = false;

// ---------------- WebSocket ----------------

socket.on("connect",    () => $("conn-status").textContent = "Connecté");
socket.on("disconnect", () => $("conn-status").textContent = "Déconnecté…");
socket.on("config", (cfg) => fillConfig(cfg));
socket.on("state",  (s)   => updateLive(s));

function fillConfig(cfg) {
  cfg = cfg || {};
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el) return;
    // Use cfg value, fall back to hardcoded default if missing
    let v = cfg[f];
    if (v === undefined || v === null) v = CFG_DEFAULTS[f];
    if (v === undefined) return;
    if (el.type === "checkbox") {
      el.checked = !!v;
    } else {
      el.value = v;
    }
  });

  const mode = cfg?.can?.can1_mode || "pcm";
  currentMode = mode;
  document.querySelectorAll('input[name="can1_mode"]').forEach(r => r.checked = (r.value === mode));

  currentListenOnly = !!cfg?.can?.can1_listen_only;
  const lo = $("cfg-can1_listen_only");
  if (lo) lo.checked = currentListenOnly;

  const airbag = cfg?.safety?.forbidden_can_ids || [];
  const airbagEl = $("safety-airbag-list");
  if (airbagEl) {
    airbagEl.textContent = airbag.length
      ? airbag.map(id => "0x" + id.toString(16).toUpperCase().padStart(3, "0")).join(", ")
      : "(none — DANGER)";
  }

  applyModeUI(mode);
}

function applyModeUI(mode) {
  $("card-pcm").style.display  = (mode === "pcm")        ? "" : "none";
  $("card-obd2").style.display = (mode === "diagnostic") ? "" : "none";

  // In PCM mode: listen-only switch is locked ON (driven by mode, not user)
  const wrap = $("switch-listen-only-wrap");
  const cb = $("cfg-can1_listen_only");
  const help = $("listen-only-help");
  if (mode === "pcm") {
    wrap.classList.add("locked");
    cb.checked = true;
    cb.disabled = true;
    help.textContent = "Mode PCM = RX-only verrouillé au niveau driver. Switch désactivé (mode PCM domine).";
  } else {
    wrap.classList.remove("locked");
    cb.disabled = false;
    cb.checked = currentListenOnly;
    help.textContent = "Mode Diagnostic : armable manuellement pour bloquer toute écriture UDS.";
  }
}

function updateLive(s) {
  // Cluster (CAN0) mini-live
  $("live-lever").textContent = s.lever || "—";
  $("live-temp").textContent  = (s.last_motor09_temp_c !== undefined ? s.last_motor09_temp_c.toFixed(1) : "—") + " °C";
  $("live-byte").textContent  = "0x" + (s.last_motor09_byte || 0).toString(16).toUpperCase().padStart(2, "0");
  $("live-tx").textContent    = s.tx_count;
  if ($("live-rxp")) $("live-rxp").textContent = s.rx_can1_count;

  // Mode pill
  const pill = $("mode-pill");
  pill.textContent = s.mode;
  pill.className = "pill pill-" + s.mode.toLowerCase();

  // CAN1 status text
  const statusEl = $("can1-status");
  if (statusEl && s.can1_mode) {
    let txt = "Actif : CAN1 → " + (s.can1_mode === "pcm" ? "PCM (RX-only)" : "Diagnostic");
    if (s.can1_mode === "diagnostic" && s.can1_listen_only) txt += " — LISTEN-ONLY armé";
    statusEl.textContent = txt;
  }

  // PCM live data
  const fmt = (v, unit, age) => (v === null || v === undefined)
    ? `— ${unit}`
    : `${v.toFixed(1)} ${unit}`;
  $("pcm-map").textContent     = fmt(s.pcm_map_mbar, "mbar");
  $("pcm-coolant").textContent = fmt(s.pcm_coolant_real_c, "°C");
  $("pcm-haldex").textContent  = fmt(s.pcm_haldex_demand_pct, "%");
  $("pcm-map-age").textContent     = s.pcm_map_age_s     != null ? `âge ${s.pcm_map_age_s.toFixed(1)}s`     : "";
  $("pcm-coolant-age").textContent = s.pcm_coolant_age_s != null ? `âge ${s.pcm_coolant_age_s.toFixed(1)}s` : "";
  $("pcm-haldex-age").textContent  = s.pcm_haldex_age_s  != null ? `âge ${s.pcm_haldex_age_s.toFixed(1)}s`  : "";

  // Safety counters
  if (s.blocked_airbag       !== undefined) $("safety-blocked-airbag").textContent = s.blocked_airbag;
  if (s.blocked_pcm_mode     !== undefined) $("safety-blocked-pcm").textContent    = s.blocked_pcm_mode;
  if (s.blocked_listen_only  !== undefined) $("safety-blocked-listen").textContent = s.blocked_listen_only;

  // Transmission Input card
  updateTransmissionDisplay(s);

  // Sync test mode UI if state changed externally (multi-browser)
  if (typeof s.test_mode_active === "boolean" && s.test_mode_active !== testmodeActive) {
    testmodeActive = s.test_mode_active;
    applyTestmodeUI();
  }
}

// ---------------- Save / Reset ----------------

$("btn-save").addEventListener("click", async () => {
  const patch = {};
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el) return;
    if (el.type === "checkbox") {
      patch[f] = el.checked;
      return;
    }
    const v = el.value;
    if (v === "") return;
    if (f === "formula") patch[f] = v;
    else if (f === "scale" || f === "offset_c") patch[f] = parseFloat(v);
    else patch[f] = parseInt(v);
  });
  const r = await fetch("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(patch)
  });
  const data = await r.json();
  $("save-status").textContent = data.ok ? "✓ Enregistré." : ("Erreur: " + (data.error || ""));
  setTimeout(() => $("save-status").textContent = "", 2000);
});

$("btn-reset").addEventListener("click", async () => {
  if (!confirm("Réinitialiser aux valeurs par défaut ?")) return;
  const defaults = {
    map_min_mbar: 300, map_max_mbar: 2500,
    scale: 1.0, offset_c: 0, tx_rate_hz: 25,
  };
  await fetch("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(defaults)
  });
  fillConfig(defaults);
});

// ---------------- CAN1 mode toggle ----------------

document.querySelectorAll('input[name="can1_mode"]').forEach(radio => {
  radio.addEventListener("change", async (e) => {
    const newMode = e.target.value;
    const r = await fetch("/api/config", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ can: { can1_mode: newMode } })
    });
    const data = await r.json();
    if (data.ok) {
      currentMode = newMode;
      applyModeUI(newMode);
      $("can1-status").textContent = "✓ CAN1 basculé en " + (newMode === "pcm" ? "PCM (RX-only)" : "Diagnostic")
                                   + " — branche le câble physique en conséquence.";
    }
  });
});

// ---------------- CAN1 listen-only switch (Diagnostic mode only) ----------------

$("cfg-can1_listen_only")?.addEventListener("change", async (e) => {
  if (currentMode === "pcm") return; // ignored in PCM mode (locked)
  const on = e.target.checked;
  currentListenOnly = on;
  await fetch("/api/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ can: { can1_listen_only: on } })
  });
});

// ---------------- OBD2 buttons ----------------

document.querySelectorAll(".obd-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const action = btn.dataset.action;
    const resultEl = $("obd2-result");
    const dtcEl = $("obd2-dtc-list");
    resultEl.className = "obd2-result";
    resultEl.textContent = "⏳ Requête en cours…";

    if (action === "clear_dtcs" && !confirm("Effacer TOUS les DTC moteur ?")) {
      resultEl.textContent = "";
      return;
    }

    try {
      const r = await fetch("/api/obd2/" + action, { method: "POST" });
      const data = await r.json();
      if (!data.ok) {
        resultEl.className = "obd2-result error";
        resultEl.textContent = "❌ " + (data.error || "Erreur inconnue");
        return;
      }
      resultEl.className = "obd2-result success";

      switch (action) {
        case "read_map":
          resultEl.textContent = `✓ MAP = ${data.map_mbar.toFixed(0)} mbar (raw: ${data.raw_hex})`;
          break;
        case "read_coolant":
          resultEl.textContent = `✓ Coolant réel = ${data.coolant_real_c.toFixed(1)} °C (raw: ${data.raw_hex})`;
          break;
        case "read_dtcs":
          dtcEl.innerHTML = "";
          if (data.count === 0) {
            resultEl.textContent = "✓ Aucun DTC actif. 🎉";
          } else {
            resultEl.textContent = `✓ ${data.count} DTC trouvé${data.count > 1 ? "s" : ""}.`;
            data.dtcs.forEach(d => {
              const row = document.createElement("div");
              row.className = "dtc-row";
              row.innerHTML = `<span class="code">${d.code}</span><span class="muted">status ${d.status_hex}</span>`;
              dtcEl.appendChild(row);
            });
          }
          break;
        case "clear_dtcs":
          resultEl.textContent = "✓ DTC effacés.";
          dtcEl.innerHTML = "";
          break;
      }
    } catch (e) {
      resultEl.className = "obd2-result error";
      resultEl.textContent = "❌ Erreur réseau: " + e.message;
    }
  });
});

// ---------------- Transmission Input card ----------------

function updateTransmissionDisplay(s) {
  const lever = s.lever || "—";
  const big = $("trans-lever-big");
  if (big) big.textContent = lever;

  const pill = $("trans-tx-pill");
  if (pill) {
    pill.textContent = s.mode;
    pill.className = "pill pill-" + s.mode.toLowerCase();
  }

  const age = $("trans-age");
  if (age) {
    if (s.lever_age_s == null) {
      age.textContent = "En attente d'une frame WBA_03 (0x394) sur Cluster CAN…";
    } else {
      const txt = s.mode === "BOOST"
        ? `✅ TX Motor_09 ACTIF — boost mappé envoyé au cluster (frame WBA_03 vue il y a ${s.lever_age_s}s)`
        : (s.mode === "TEMP"
            ? `🟢 TX silencieux — gateway forwarde vraie temp (frame vue il y a ${s.lever_age_s}s)`
            : `⏸ En attente WBA_03 (frame vue il y a ${s.lever_age_s}s)`);
      age.textContent = txt;
    }
  }
}

// ---------------- Frame Log ----------------

let framelogPaused = false;
let currentChannel = "cluster";
let currentView = "agg";
let framelogTimer = null;

function startFramelogPolling() {
  if (framelogTimer) clearInterval(framelogTimer);
  framelogTimer = setInterval(refreshFramelog, 500);
  refreshFramelog();
}

async function refreshFramelog() {
  if (framelogPaused) return;
  const url = `/api/framelog/${currentChannel}?mode=${currentView}`;
  try {
    const r = await fetch(url);
    const data = await r.json();
    if (!data.ok) return;
    renderFramelog(data.frames);
  } catch (e) {
    // ignore network errors during polling
  }
}

function renderFramelog(frames) {
  const thead = $("framelog-thead");
  const tbody = $("framelog-tbody");
  const cnt = $("framelog-count");
  if (cnt) cnt.textContent = `(${frames.length})`;

  if (!frames.length) {
    thead.innerHTML = "";
    tbody.innerHTML = `<tr><td class="muted small">En attente de frames sur ${currentChannel}…</td></tr>`;
    return;
  }

  if (currentView === "agg") {
    thead.innerHTML = `<tr><th>ID</th><th>Dir</th><th>Count</th><th>Age</th><th>Last data</th></tr>`;
    tbody.innerHTML = frames.map(f => `
      <tr>
        <td class="framelog-id">${f.id_hex}</td>
        <td class="framelog-dir-${f.dir}">${f.dir.toUpperCase()}</td>
        <td class="framelog-count">${f.count}</td>
        <td class="framelog-age">${f.age_s}s</td>
        <td class="framelog-data">${f.last_data}</td>
      </tr>
    `).join("");
  } else {
    thead.innerHTML = `<tr><th>Time</th><th>ID</th><th>Dir</th><th>Data</th></tr>`;
    // chronological — most recent first
    const sorted = [...frames].reverse().slice(0, 50);
    tbody.innerHTML = sorted.map(f => {
      const t = new Date(f.ts * 1000);
      const tstr = t.toLocaleTimeString("fr-CA", { hour12: false }) +
                   "." + String(t.getMilliseconds()).padStart(3, "0");
      return `
        <tr>
          <td class="muted">${tstr}</td>
          <td class="framelog-id">${f.id_hex}</td>
          <td class="framelog-dir-${f.dir}">${f.dir.toUpperCase()}</td>
          <td class="framelog-data">${f.data}</td>
        </tr>
      `;
    }).join("");
  }
}

document.querySelectorAll('input[name="framelog_channel"]').forEach(r => {
  r.addEventListener("change", (e) => {
    currentChannel = e.target.value;
    refreshFramelog();
  });
});

document.querySelectorAll('input[name="framelog_view"]').forEach(r => {
  r.addEventListener("change", (e) => {
    currentView = e.target.value;
    refreshFramelog();
  });
});

$("btn-framelog-pause")?.addEventListener("click", async () => {
  framelogPaused = !framelogPaused;
  $("btn-framelog-pause").textContent = framelogPaused ? "▶ Reprendre" : "⏸ Pause";
  await fetch("/api/framelog/pause", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ paused: framelogPaused })
  });
});

$("btn-framelog-clear")?.addEventListener("click", async () => {
  await fetch("/api/framelog/clear", { method: "POST" });
  refreshFramelog();
});

// ---------------- Test Mode ----------------

// Same formula as backend: byte = (temp_c + 43.94) / 0.7339
function tempCToByte(t) {
  let raw = (parseFloat(t) + 43.94) / 0.7339;
  raw = Math.round(raw);
  return Math.max(0, Math.min(255, raw));
}

function refreshTestmodeBytePreview() {
  const tInput = $("testmode-temp");
  if (!tInput) return;
  const b = tempCToByte(tInput.value);
  $("testmode-byte-preview").value =
    "0x" + b.toString(16).toUpperCase().padStart(2, "0") + " (" + b + ")";
}

$("testmode-temp")?.addEventListener("input", refreshTestmodeBytePreview);

document.querySelectorAll(".preset-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    $("testmode-temp").value = btn.dataset.temp;
    refreshTestmodeBytePreview();
    // If test mode already active, push the new temp immediately
    if (testmodeActive) sendTestMode(true);
  });
});

let testmodeActive = false;

async function sendTestMode(active) {
  const temp = parseFloat($("testmode-temp").value) || 90;
  const r = await fetch("/api/test_mode", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ active: active, temp_c: temp })
  });
  const data = await r.json();
  if (data.ok) {
    testmodeActive = data.test_mode_active;
    applyTestmodeUI();
  }
}

function applyTestmodeUI() {
  const banner = $("testmode-banner");
  const startBtn = $("btn-testmode-toggle");
  const stopBtn = $("btn-testmode-stop");
  const status = $("testmode-status");
  if (testmodeActive) {
    banner.style.display = "block";
    startBtn.textContent = "🔄 Mettre à jour température";
    stopBtn.style.display = "block";
    status.textContent = "✅ TX en cours vers le cluster...";
    status.style.color = "var(--boost)";
  } else {
    banner.style.display = "none";
    startBtn.textContent = "▶ Activer mode test";
    stopBtn.style.display = "none";
    status.textContent = "Mode test inactif.";
    status.style.color = "";
  }
}

$("btn-testmode-toggle")?.addEventListener("click", () => sendTestMode(true));
$("btn-testmode-stop")?.addEventListener("click", () => sendTestMode(false));

// Initial preview render
refreshTestmodeBytePreview();

// ---------------- Initial fetch ----------------

async function refreshAllFromServer() {
  const banner = $("fetch-error-banner");
  const detail = $("fetch-error-detail");
  try {
    // Cache-buster timestamp + cache:no-store header — DEFENSIVE against
    // iPhone Safari which is known to cache GET responses aggressively
    // even with no-cache headers (especially on first reload).
    const r = await fetch("/api/config?_=" + Date.now(), {
      cache: "no-store",
      headers: { "Cache-Control": "no-cache" }
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const cfg = await r.json();
    fillConfig(cfg);
    if (banner) banner.style.display = "none";
  } catch (e) {
    console.error("Could not fetch /api/config:", e);
    if (banner) {
      banner.style.display = "block";
      if (detail) detail.textContent = "Erreur: " + e.message;
    }
    fillConfig({});
  }
}

// First load
(async () => {
  await refreshAllFromServer();
  startFramelogPolling();
})();

// Mobile browser bfcache restore: when the user navigates back to the page,
// the JS may not re-run. pageshow with persisted=true means we came from cache.
// Re-fetch to ensure inputs are populated.
window.addEventListener("pageshow", (e) => {
  if (e.persisted) {
    console.log("pageshow from bfcache — refetching config");
    refreshAllFromServer();
  }
});

// Also refetch when tab becomes visible (catches some Android Chrome cases)
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshAllFromServer();
  }
});
