"""MK7BoostGauge entry point — wires config + CAN + boost logic + web UI."""
from __future__ import annotations

import logging
import signal
import sys

from .boost_logic import BoostController
from .can_manager import CanManager
from .config import Config
from .webserver import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("MK7BoostGauge")


def main() -> int:
    log.info("MK7BoostGauge starting")

    cfg = Config()
    log.info("Config loaded: %s", cfg.data)

    can_mgr = CanManager(
        cluster_iface=cfg["can"]["cluster_iface"],
        can1_iface=cfg["can"]["can1_iface"],
        bitrate=cfg["can"]["bitrate"],
        forbidden_ids=set(cfg["safety"]["forbidden_can_ids"]),
        can1_listen_only=cfg["can"].get("can1_listen_only", False),
        can1_mode=cfg["can"].get("can1_mode", "pcm"),
    )
    can_mgr.open()

    controller = BoostController(cfg, can_mgr)
    controller.start()

    app, socketio = create_app(cfg, controller)

    def _shutdown(*_):
        log.info("Shutdown requested")
        controller.stop()
        can_mgr.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Web UI on http://0.0.0.0:80")
    socketio.run(app, host="0.0.0.0", port=80, debug=False, use_reloader=False,
                 allow_unsafe_werkzeug=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
