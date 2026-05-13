/* MK7BoostGauge UI v2 — Socket.IO + REST + OBD2 tool + PCM live */

const $ = (id) => document.getElementById(id);
const socket = io();

const CFG_FIELDS = ["map_min_mbar", "map_max_mbar", "temp_min_c", "temp_max_c",
                    "scale", "offset_c", "tx_rate_hz", "formula"];

let currentMode = "pcm";
let currentListenOnly = false;

// ---------------- WebSocket ----------------

socket.on("connect",    () => $("conn-status").textContent = "Connecté");
socket.on("disconnect", () => $("conn-status").textContent = "Déconnecté…");
socket.on("config", (cfg) => fillConfig(cfg));
socket.on("state",  (s)   => updateLive(s));

function fillConfig(cfg) {
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (el && cfg[f] !== undefined) el.value = cfg[f];
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
}

// ---------------- Save / Reset ----------------

$("btn-save").addEventListener("click", async () => {
  const patch = {};
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el) return;
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
    temp_min_c: 50, temp_max_c: 130,
    scale: 1.0, offset_c: 0, tx_rate_hz: 25, formula: "linear"
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

// ---------------- Initial fetch ----------------

(async () => {
  const r = await fetch("/api/config");
  const cfg = await r.json();
  fillConfig(cfg);
})();
