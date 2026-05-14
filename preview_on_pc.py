"""
MK7BoostGauge — Preview the web UI on your PC (Windows / Mac / Linux).

Use this to visualize the interface BEFORE deploying to the Pi.
NO Pi required, NO CAN HAT required, NO root needed.

The CAN buses fail to open (stub mode) but the web UI is fully functional:
  - All cards visible and styled
  - Save/Reset works (persisted to local file 'preview_config.json')
  - Test mode buttons work (no real TX since no CAN bus)
  - Frame Log will be empty (no real frames)
  - OBD2 buttons return 504 timeout (no real bus to query)

Usage (from this directory, on your PC):

    pip install -r requirements.txt
    python preview_on_pc.py

Then open http://localhost:8080 in your browser (auto-opens).
Press Ctrl+C in the terminal to stop.
"""
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Use a LOCAL config file in this directory (not /var/lib/boostgauge)
HERE = Path(__file__).parent.resolve()
PREVIEW_CONFIG = HERE / "preview_config.json"
os.environ["BOOSTGAUGE_CONFIG"] = str(PREVIEW_CONFIG)

# Make sure we can import app/ as a module
sys.path.insert(0, str(HERE))

print("=" * 60)
print(" MK7BoostGauge — PC preview mode")
print("=" * 60)
print(f" Config file: {PREVIEW_CONFIG}")
print(f" Port: 8080 (no admin required)")
print()

from app.config import Config
from app.can_manager import CanManager
from app.boost_logic import BoostController
from app.webserver import create_app

cfg = Config()
print(" Loaded config:")
for k in ("map_min_mbar", "map_max_mbar", "scale", "offset_c", "tx_rate_hz"):
    print(f"   {k} = {cfg.data.get(k)}")
print()

# Stub-mode CAN — bus opens will fail on Windows/Mac, that's expected
mgr = CanManager(
    cluster_iface=cfg["can"]["cluster_iface"],
    can1_iface=cfg["can"]["can1_iface"],
    bitrate=cfg["can"]["bitrate"],
    forbidden_ids=set(cfg["safety"]["forbidden_can_ids"]),
    can1_listen_only=cfg["can"].get("can1_listen_only", False),
    can1_mode=cfg["can"].get("can1_mode", "diagnostic"),
)
mgr.open()
print(" CAN buses: stub mode (no hardware) — UI fully functional, no actual TX/RX")
print()

ctrl = BoostController(cfg, mgr)
ctrl.start()

app, socketio = create_app(cfg, ctrl)

# Auto-open browser after 2 sec
def open_browser():
    time.sleep(2)
    url = "http://localhost:8080"
    print(f" Opening {url} in your default browser...")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f" (could not auto-open browser: {e}; open {url} manually)")

threading.Thread(target=open_browser, daemon=True).start()

print(" Starting web server. Press Ctrl+C to stop.")
print("=" * 60)
print()

try:
    socketio.run(
        app,
        host="127.0.0.1",
        port=8080,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
except KeyboardInterrupt:
    print("\n Stopped by user. Goodbye.")
    ctrl.stop()
    mgr.close()
