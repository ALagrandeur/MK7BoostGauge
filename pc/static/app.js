/* MK7BoostGauge v3 - PC-hosted UI logic */

const $ = (id) => document.getElementById(id);

const CFG_FIELDS = ["map_min_mbar", "map_max_mbar", "scale", "offset_c",
                    "tx_rate_hz", "test_mode_temp_c", "pi_host", "pi_port"];

// Hardcoded defaults (fallback if backend returns sparse config)
const CFG_DEFAULTS = {
  map_min_mbar: 300, map_max_mbar: 2500,
  scale: 1.0, offset_c: 0, tx_rate_hz: 25,
  test_mode_temp_c: 90,
  pi_host: "boostgauge.local", pi_port: 8765,
};

let testModeActive = false;
let pcConfigCache = {};

// ============================================================
//  Load PC config from server (pc_config.json)
// ============================================================

async function loadPcConfig() {
  try {
    const r = await fetch("/api/pc_config?_=" + Date.now(), { cache: "no-store" });
    const cfg = await r.json();
    pcConfigCache = cfg;
    fillFields(cfg);
    testModeActive = !!cfg.test_mode_active;
    applyTestmodeUI();
    return cfg;
  } catch (e) {
    console.error("Could not load PC config:", e);
    fillFields(CFG_DEFAULTS);
    return CFG_DEFAULTS;
  }
}

function fillFields(cfg) {
  cfg = cfg || {};
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el) return;
    let v = cfg[f];
    if (v === undefined || v === null) v = CFG_DEFAULTS[f];
    if (v === undefined) return;
    el.value = v;
  });
}

function readFieldsAsPatch() {
  const patch = {};
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el || el.value === "") return;
    if (f === "pi_host") patch[f] = el.value.trim();
    else if (f === "pi_port") patch[f] = parseInt(el.value);
    else if (f === "scale" || f === "offset_c" || f === "test_mode_temp_c")
      patch[f] = parseFloat(el.value);
    else patch[f] = parseInt(el.value);
  });
  return patch;
}

// ============================================================
//  Save PC config (auto on every field change)
// ============================================================

async function savePcConfig(patch) {
  Object.assign(pcConfigCache, patch);
  try {
    const r = await fetch("/api/pc_config", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(patch),
    });
    return await r.json();
  } catch (e) {
    console.error("Save PC config failed:", e);
    return { ok: false, error: e.message };
  }
}

// Auto-save on field blur (so settings persist even without "Send")
document.addEventListener("blur", (e) => {
  if (e.target && e.target.id && e.target.id.startsWith("cfg-")) {
    const patch = readFieldsAsPatch();
    savePcConfig(patch);
  }
}, true);

// ============================================================
//  Send to Pi (main button)
// ============================================================

async function sendToPi(extraPatch = {}) {
  const status = $("send-status");
  status.className = "send-status";
  status.textContent = "⏳ Envoi en cours...";

  // First save fields locally
  const patch = { ...readFieldsAsPatch(), ...extraPatch };
  await savePcConfig(patch);

  try {
    const r = await fetch("/api/send_to_pi", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(patch),
    });
    const data = await r.json();
    if (data.ok) {
      status.className = "send-status ok";
      status.textContent = "✓ " + (data.message || "Envoyé au Pi");
      pingPi();
    } else {
      status.className = "send-status fail";
      status.textContent = "✗ " + (data.message || "Échec envoi");
    }
    return data;
  } catch (e) {
    status.className = "send-status fail";
    status.textContent = "✗ Erreur réseau: " + e.message;
    return { ok: false };
  }
}

$("btn-send").addEventListener("click", () => sendToPi());

// ============================================================
//  Test mode (LIVE - send on every preset click)
// ============================================================

function applyTestmodeUI() {
  const banner = $("testmode-banner");
  const startBtn = $("btn-testmode-toggle");
  const stopBtn = $("btn-testmode-stop");
  const status = $("testmode-status");
  const tempInput = $("cfg-test_mode_temp_c");

  if (testModeActive) {
    banner.style.display = "block";
    $("testmode-active-temp").textContent = tempInput ? tempInput.value : "90";
    startBtn.textContent = "🔄 Mettre à jour température";
    stopBtn.style.display = "block";
    status.textContent = "✅ Test mode actif - chaque preset envoie au Pi instantanément";
    status.style.color = "var(--boost)";
  } else {
    banner.style.display = "none";
    startBtn.textContent = "▶ Activer mode test";
    stopBtn.style.display = "none";
    status.textContent = "Mode test inactif.";
    status.style.color = "";
  }
}

async function setTestMode(active, temp_c = null) {
  testModeActive = active;
  const patch = { test_mode_active: active };
  if (temp_c !== null) patch.test_mode_temp_c = parseFloat(temp_c);
  await sendToPi(patch);
  applyTestmodeUI();
}

$("btn-testmode-toggle").addEventListener("click", () => {
  setTestMode(true);
});

$("btn-testmode-stop").addEventListener("click", () => {
  setTestMode(false);
});

// Preset buttons - LIVE: each click sends immediately
document.querySelectorAll(".preset-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const t = parseFloat(btn.dataset.temp);
    $("cfg-test_mode_temp_c").value = t;
    // If test mode is active, send immediately. Otherwise just fill the field.
    if (testModeActive) {
      setTestMode(true, t);
    } else {
      savePcConfig({ test_mode_temp_c: t });
    }
  });
});

// ============================================================
//  Pi ping + live status polling
// ============================================================

async function pingPi() {
  const pill = $("pi-status-pill");
  const result = $("ping-result");
  try {
    const r = await fetch("/api/pi_ping", { cache: "no-store" });
    const data = await r.json();
    if (data.ok) {
      pill.textContent = "Pi: OK";
      pill.className = "pill pill-ok";
      if (result) result.textContent = `✓ ${data.message} @ ${data.host}`;
    } else {
      pill.textContent = "Pi: KO";
      pill.className = "pill pill-fail";
      if (result) result.textContent = `✗ ${data.message} @ ${data.host}`;
    }
  } catch (e) {
    pill.textContent = "Pi: ?";
    pill.className = "pill pill-fail";
    if (result) result.textContent = "✗ Erreur réseau";
  }
}

$("btn-test-pi").addEventListener("click", pingPi);

async function pollPiStatus() {
  try {
    const r = await fetch("/api/pi_status", { cache: "no-store" });
    const env = await r.json();
    if (!env.ok || !env.data) {
      $("live-lever").textContent = "—";
      $("live-mode").textContent = "—";
      $("live-mode").className = "value-big";
      $("live-byte").textContent = "0x—";
      $("live-temp").textContent = "— °C";
      $("live-age").textContent = "Pi pas joignable";
      return;
    }
    const s = env.data;
    $("live-lever").textContent = s.lever || "—";
    $("live-mode").textContent = s.mode || "—";
    $("live-mode").className = "value-big mode-" + (s.mode || "WAITING");
    $("live-byte").textContent = "0x" + (s.last_motor09_byte || 0).toString(16).toUpperCase().padStart(2, "0");
    $("live-temp").textContent = (s.last_motor09_temp_c !== undefined ? s.last_motor09_temp_c.toFixed(1) : "—") + " °C";
    if (s.can) {
      $("live-tx").textContent = s.can.tx_count;
      $("live-rx").textContent = s.can.rx_cluster_count;
    }
    if (s.lever_age_s != null) {
      $("live-age").textContent = "Dernière frame WBA_03: " + s.lever_age_s.toFixed(1) + "s";
    } else {
      $("live-age").textContent = "Aucune frame WBA_03 reçue";
    }
  } catch (e) {
    // silent — polling can fail occasionally
  }
}

// ============================================================
//  Initial load + start polling
// ============================================================

(async () => {
  await loadPcConfig();
  pingPi();
  pollPiStatus();
  // Poll Pi status every 1 sec
  setInterval(pollPiStatus, 1000);
  // Ping Pi every 5 sec to keep status pill fresh
  setInterval(pingPi, 5000);
})();
