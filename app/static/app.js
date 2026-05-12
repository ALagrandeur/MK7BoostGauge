/* MK7BoostGauge UI — Socket.IO + REST */

const $ = (id) => document.getElementById(id);
const socket = io();

const CFG_FIELDS = ["map_min_mbar", "map_max_mbar", "temp_min_c", "temp_max_c",
                    "scale", "offset_c", "tx_rate_hz"];

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
  // CAN1 mode toggle
  const mode = cfg?.can?.can1_mode || "pcm";
  document.querySelectorAll('input[name="can1_mode"]').forEach(r => {
    r.checked = (r.value === mode);
  });
}

function updateLive(s) {
  $("live-lever").textContent = s.lever || "—";
  $("live-map").textContent   = (s.map_mbar?.toFixed(0) || "—") + " mbar";
  $("live-mapsrc").textContent = s.map_source_active || "—";
  $("live-temp").textContent  = (s.last_motor09_temp_c?.toFixed(1) || "—") + " °C";
  $("live-byte").textContent  = "0x" + (s.last_motor09_byte || 0).toString(16).toUpperCase().padStart(2, "0");
  $("live-tx").textContent    = s.tx_count;
  $("live-rxp").textContent   = s.rx_can1_count;
  $("live-rxc").textContent   = s.rx_cluster_count;
  $("live-mapage").textContent = (s.map_age_s !== null && s.map_age_s !== undefined)
    ? s.map_age_s.toFixed(1) + " s"
    : "— s";

  const pill = $("mode-pill");
  pill.textContent = s.mode;
  pill.className = "pill pill-" + s.mode.toLowerCase();

  // Reflect actual can1_mode into status text
  const statusEl = $("can1-status");
  if (statusEl && s.can1_mode) {
    statusEl.textContent = "Actif : CAN1 → " + (s.can1_mode === "pcm" ? "PCM" : "Diagnostic");
  }
}

// ---------------- Save / Reset ----------------

$("btn-save").addEventListener("click", async () => {
  const patch = {};
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el) return;
    const v = el.value;
    if (v === "") return;
    patch[f] = (f === "scale" || f === "offset_c") ? parseFloat(v) : parseInt(v);
  });
  const r = await fetch("/api/config", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
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
    scale: 1.0, offset_c: 0, tx_rate_hz: 25
  };
  await fetch("/api/config", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(defaults)
  });
  fillConfig(defaults);
});

// ---------------- CAN1 mode toggle (hot-applied, no reboot) ----------------

document.querySelectorAll('input[name="can1_mode"]').forEach(radio => {
  radio.addEventListener("change", async (e) => {
    const newMode = e.target.value;
    const r = await fetch("/api/config", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ can: { can1_mode: newMode } })
    });
    const data = await r.json();
    const status = $("can1-status");
    if (data.ok) {
      status.textContent = "✓ CAN1 basculé en mode " + (newMode === "pcm" ? "PCM" : "Diagnostic")
                         + " — branche le câble physique en conséquence.";
    } else {
      status.textContent = "Erreur: " + (data.error || "?");
    }
  });
});

// ---------------- Initial fetch ----------------

(async () => {
  const r = await fetch("/api/config");
  const cfg = await r.json();
  fillConfig(cfg);
})();
