/* MK7BoostGauge v3.1 - PC-hosted UI logic */

const $ = (id) => document.getElementById(id);

const CFG_FIELDS = [
  // Cluster mapping
  "map_min_mbar", "map_max_mbar", "scale", "offset_c", "tx_rate_hz",
  // Cluster CAN addresses
  "cluster_motor09_id_hex", "cluster_wba03_id_hex",
  // MAP source
  "map_source",
  "obd2_req_id_hex", "obd2_resp_id_hex", "obd2_did_map_hex", "obd2_query_rate_hz",
  "pcm_map_can_id_hex", "pcm_map_byte_offset", "pcm_map_scale", "pcm_map_offset",
  // Pi connection
  "pi_host", "pi_port",
];

const CFG_DEFAULTS = {
  map_min_mbar: 300, map_max_mbar: 2500,
  scale: 1.0, offset_c: 0, tx_rate_hz: 25,
  cluster_motor09_id_hex: "0x647", cluster_wba03_id_hex: "0x394",
  map_source: "obd2_diagnostic",
  obd2_req_id_hex: "0x7E0", obd2_resp_id_hex: "0x7E8",
  obd2_did_map_hex: "0x39C0", obd2_query_rate_hz: 5,
  pcm_map_can_id_hex: "0x0", pcm_map_byte_offset: 0,
  pcm_map_scale: 1.0, pcm_map_offset: 0.0,
  pi_host: "boostgauge.local", pi_port: 8765,
};

let pcConfigCache = {};

// ============================================================
//  Load + save PC config
// ============================================================

async function loadPcConfig() {
  try {
    const r = await fetch("/api/pc_config?_=" + Date.now(), { cache: "no-store" });
    const cfg = await r.json();
    pcConfigCache = cfg;
    fillFields(cfg);
    applyMapSourceUI(cfg.map_source);
    return cfg;
  } catch (e) {
    console.error("Could not load PC config:", e);
    fillFields(CFG_DEFAULTS);
    applyMapSourceUI(CFG_DEFAULTS.map_source);
    return CFG_DEFAULTS;
  }
}

function fillFields(cfg) {
  cfg = cfg || {};
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el) {
      // map_source is radio buttons, not a single id
      if (f === "map_source") {
        const val = cfg[f] !== undefined ? cfg[f] : CFG_DEFAULTS[f];
        document.querySelectorAll('input[name="map_source"]').forEach(r => {
          r.checked = (r.value === val);
        });
      }
      return;
    }
    let v = cfg[f];
    if (v === undefined || v === null) v = CFG_DEFAULTS[f];
    if (v === undefined) return;
    el.value = v;
  });
}

function readFieldsAsPatch() {
  const patch = {};
  // text/number fields
  CFG_FIELDS.forEach(f => {
    const el = $("cfg-" + f);
    if (!el || el.value === "") return;
    const v = el.value;
    if (f === "pi_host" || f === "obd2_req_id_hex" || f === "obd2_resp_id_hex"
        || f === "obd2_did_map_hex" || f === "pcm_map_can_id_hex"
        || f === "cluster_motor09_id_hex" || f === "cluster_wba03_id_hex") {
      patch[f] = v.trim();
    } else if (f === "pi_port" || f === "tx_rate_hz" || f === "obd2_query_rate_hz"
               || f === "pcm_map_byte_offset" || f === "map_min_mbar"
               || f === "map_max_mbar" || f === "offset_c") {
      patch[f] = parseInt(v);
    } else {
      patch[f] = parseFloat(v);
    }
  });
  // map_source from radio
  const checked = document.querySelector('input[name="map_source"]:checked');
  if (checked) patch.map_source = checked.value;
  return patch;
}

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

// Auto-save when a field loses focus + update preview
document.addEventListener("blur", (e) => {
  if (e.target && e.target.id && e.target.id.startsWith("cfg-")) {
    savePcConfig(readFieldsAsPatch());
    updateMappingPreview();
  }
}, true);

// Update preview on every keystroke for mapping fields
["map_min_mbar", "map_max_mbar", "scale", "offset_c"].forEach(f => {
  const el = $("cfg-" + f);
  if (el) el.addEventListener("input", updateMappingPreview);
});

// ============================================================
//  Mapping preview - compute what the cluster will display
//  Mirror of pi/vw_signals.py map_mbar_to_motor09_byte
// ============================================================

const DEAD_ZONE_LOW = 80.0;
const DEAD_ZONE_HIGH = 110.0;
const SAFE_MARGIN = 1.0;
const TEMP_MIN = 50.0;
const TEMP_MAX = 130.0;

function mapMbarToTempC(mapMbar, mapMin, mapMax, scale, offsetC) {
  let ratio;
  if (mapMax === mapMin) ratio = 0.5;
  else {
    ratio = (mapMbar - mapMin) / (mapMax - mapMin);
    if (ratio < 0) ratio = 0;
    if (ratio > 1) ratio = 1;
  }
  // Dead zone skip
  const safeLow = DEAD_ZONE_LOW - SAFE_MARGIN;
  const safeHigh = DEAD_ZONE_HIGH + SAFE_MARGIN;
  const lenBottom = safeLow - TEMP_MIN;
  const lenTop = TEMP_MAX - safeHigh;
  const total = lenBottom + lenTop;
  let tempC;
  if (total <= 0) tempC = (TEMP_MIN + TEMP_MAX) / 2;
  else {
    const usefulPos = ratio * total;
    if (usefulPos <= lenBottom) tempC = TEMP_MIN + usefulPos;
    else tempC = safeHigh + (usefulPos - lenBottom);
  }
  return tempC * scale + offsetC;
}

function tempCToByte(t) {
  let raw = Math.round((t + 43.94) / 0.7339);
  if (raw < 0) raw = 0;
  if (raw > 255) raw = 255;
  return raw;
}

function updateMappingPreview() {
  const tbody = $("mapping-tbody");
  if (!tbody) return;
  const mapMin = parseFloat($("cfg-map_min_mbar").value) || 300;
  const mapMax = parseFloat($("cfg-map_max_mbar").value) || 2500;
  const scale  = parseFloat($("cfg-scale").value) || 1.0;
  const offset = parseFloat($("cfg-offset_c").value) || 0;

  // 5 sample points: min, 25%, 50%, 75%, max
  const points = [
    { label: "MAP min (idle)",       mbar: mapMin },
    { label: "25%",                  mbar: mapMin + (mapMax - mapMin) * 0.25 },
    { label: "50% (proche frontière)", mbar: mapMin + (mapMax - mapMin) * 0.5 },
    { label: "75%",                  mbar: mapMin + (mapMax - mapMin) * 0.75 },
    { label: "MAP max (boost full)", mbar: mapMax },
  ];

  let html = "";
  for (const p of points) {
    const tempC = mapMbarToTempC(p.mbar, mapMin, mapMax, scale, offset);
    const byte = tempCToByte(tempC);
    const needlePct = Math.max(0, Math.min(100, (tempC - TEMP_MIN) / (TEMP_MAX - TEMP_MIN) * 100));
    const inDead = tempC >= DEAD_ZONE_LOW && tempC <= DEAD_ZONE_HIGH;
    const warning = inDead ? '<span style="color:#ff5e5e">⚠ DEAD ZONE</span>' : '';
    html += `<tr>
      <td>${p.mbar.toFixed(0)} <span class="muted small">(${p.label})</span></td>
      <td>${tempC.toFixed(1)}°C ${warning}</td>
      <td>0x${byte.toString(16).toUpperCase().padStart(2,"0")}</td>
      <td><div class="needle-bar"><div class="needle-fill" style="width:${needlePct.toFixed(0)}%"></div></div></td>
    </tr>`;
  }
  tbody.innerHTML = html;
}

// MAP source radio: save + toggle visible fields
document.querySelectorAll('input[name="map_source"]').forEach(radio => {
  radio.addEventListener("change", () => {
    applyMapSourceUI(radio.value);
    savePcConfig(readFieldsAsPatch());
  });
});

function applyMapSourceUI(source) {
  $("obd2-fields").style.display = (source === "obd2_diagnostic") ? "" : "none";
  $("pcm-fields").style.display = (source === "pcm_broadcast") ? "" : "none";
  $("live-map-label").textContent = (source === "obd2_diagnostic")
    ? "MAP (CAN1 OBD2)" : "MAP (CAN1 PCM broadcast)";
}

// ============================================================
//  Send to Pi
// ============================================================

async function sendToPi() {
  const status = $("send-status");
  status.className = "send-status";
  status.textContent = "⏳ Envoi en cours...";

  const patch = readFieldsAsPatch();
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
      pollPiStatus();
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

$("btn-send").addEventListener("click", sendToPi);

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
      $("live-mode").textContent = "Pi pas joignable";
      $("live-map").textContent = "—";
      $("live-map-age").textContent = "—";
      $("live-coolant").textContent = "—";
      $("live-coolant-age").textContent = "—";
      return;
    }
    const s = env.data;
    // Lever + mode
    $("live-lever").textContent = s.lever || "—";
    $("live-mode").textContent = s.mode ? `Mode: ${s.mode}` : "—";

    // MAP
    if (s.map_mbar !== null && s.map_mbar !== undefined) {
      $("live-map").textContent = s.map_mbar.toFixed(0) + " mbar";
      const age = s.map_age_s != null ? s.map_age_s.toFixed(1) + "s" : "?";
      $("live-map-age").textContent = "âge: " + age;
    } else {
      $("live-map").textContent = "— mbar";
      $("live-map-age").textContent = "pas de donnée";
    }

    // Coolant real (sniffed from CAN0 Motor_09 byte 0)
    if (s.coolant_real_c !== null && s.coolant_real_c !== undefined) {
      $("live-coolant").textContent = s.coolant_real_c.toFixed(1) + " °C";
      const age = s.coolant_age_s != null ? s.coolant_age_s.toFixed(1) + "s" : "?";
      $("live-coolant-age").textContent = "âge: " + age;
    } else {
      $("live-coolant").textContent = "— °C";
      $("live-coolant-age").textContent = "pas de frame Motor_09";
    }
  } catch (e) {
    // silent
  }
}

$("btn-refresh").addEventListener("click", () => {
  pollPiStatus();
  pingPi();
});

// ============================================================
//  Initial load + polling
// ============================================================

(async () => {
  await loadPcConfig();
  updateMappingPreview();             // initial mapping preview
  pingPi();
  pollPiStatus();
  setInterval(pollPiStatus, 1000);   // 1Hz live status
  setInterval(pingPi, 5000);          // 0.2Hz pi ping
})();
