"""MK7BoostGauge v3 - PC launcher.

Starts the Flask server on localhost:8080 and opens the browser automatically.
Run this directly OR via the desktop shortcut (created by install_shortcut.py).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Add project root so we can import pc.app
HERE = Path(__file__).parent.resolve()
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pc.app.config import PcConfig
from pc.app.server import create_app

LOGGER = logging.getLogger("MK7BoostGauge-PC")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print(" MK7BoostGauge v3 - PC UI")
    print("=" * 60)
    print()

    config = PcConfig()
    print(f" Config file: {config.path}")
    print(f" Pi target  : http://{config.data.get('pi_host')}:{config.data.get('pi_port')}")
    print()

    app = create_app(config)

    # Auto-pick first available port starting at 8080
    import socket
    port = 8080
    for candidate in (8080, 8081, 8082, 8083, 8084):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                port = candidate
                break
            except OSError:
                continue
    else:
        print(f" ERROR: ports 8080-8084 all busy. Close other apps or pick another port.")
        return 1

    if port != 8080:
        print(f" Port 8080 busy, using port {port} instead")

    url = f"http://localhost:{port}"
    print(f" Web UI starting at {url}")
    print(f" Press Ctrl+C to stop")
    print("=" * 60)
    print()

    # Open browser after 1.5s (let Flask start first)
    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f" (could not auto-open browser: {e}; open {url} manually)")

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        # Disable Werkzeug's annoying reload + use threaded mode
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False,
                threaded=True)
    except KeyboardInterrupt:
        print("\n Stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
