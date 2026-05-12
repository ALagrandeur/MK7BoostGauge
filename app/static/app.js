/* MK7BoostGauge UI — Socket.IO + REST */

const $ = (id) => document.getElementById(id);
const socket = io();

const CFG_FIELDS = ["map_min_mbar", "map_max_mbar", "temp_min_c", "temp_max_c",
                    "scale", "offset_c", "tx_rate_hz"];

// ---------------- WebSocket ----------------

socket.on("connect", () => {
  $("conn-status").textContent = "Connecté";
});
socket.on("disconnect", () => {
  $("conn-status").textContent = "Déconnecté…";
});

socket.on("config", (cfg) => fillConfig(cfg));
socket.on("state",  (s)   => updateLive(s));

function fillConfig(cfg) {
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (el && cfg[f] !== undefined) el.value = cfg[f];
  });
}

function updateLive(s) {
  $("live-lever").textContent = s.lever || "—";
  $("live-map").textContent   = (s.map_mbar?.toFixed(0) || "—") + " mbar";
  $("live-temp").textContent  = (s.last_motor09_temp_c?.toFixed(1) || "—") + " °C";
  $("live-byte").textContent  = "0x" + (s.last_motor09_byte || 0).toString(16).toUpperCase().padStart(2, "0");
  $("live-tx").textContent    = s.tx_count;
  $("live-rxp").textContent   = s.rx_powertrain_count;
  $("live-rxc").textContent   = s.rx_cluster_count;
  $("live-mapage").textContent = (s.map_age_s !== null && s.map_age_s !== undefined)
    ? s.map_age_s.toFixed(1) + " s"
    : "— s";

  const pill = $("mode-pill");
  pill.textContent = s.mode;
  pill.className = "pill pill-" + s.mode.toLowerCase();
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

// ---------------- Initial fetch ----------------

(async () => {
  const r = await fetch("/api/config");
  const cfg = await r.json();
  fillConfig(cfg);
})();
