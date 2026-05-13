"""Flask + SocketIO web UI — config + live telemetry + OBD2 tool."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO

from .uds import decode_did_coolant_real_c, decode_did_map_mbar

if TYPE_CHECKING:
    from .boost_logic import BoostController
    from .config import Config

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: "Config", controller: "BoostController") -> tuple[Flask, SocketIO]:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
    app.config["SECRET_KEY"] = "mk7boostgauge"
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

    # ---------------- Routes ----------------

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/api/config", methods=["GET"])
    def api_get_config():
        return jsonify(config.data)

    @app.route("/api/config", methods=["POST"])
    def api_post_config():
        patch = request.get_json(force=True)
        if not isinstance(patch, dict):
            return jsonify({"ok": False, "error": "expect JSON object"}), 400

        # SAFETY: forbidden_can_ids is hardcoded — refuse any client attempt to shrink it
        try:
            forbidden_patch = patch.get("safety", {}).get("forbidden_can_ids")
            if forbidden_patch is not None:
                current = set(config["safety"]["forbidden_can_ids"])
                proposed = set(int(x, 16) if isinstance(x, str) else int(x) for x in forbidden_patch)
                if not current.issubset(proposed):
                    log.error("REFUSED config patch trying to remove forbidden_can_ids: %s", forbidden_patch)
                    return jsonify({"ok": False, "error": "cannot remove forbidden_can_ids (airbag protection)"}), 403
        except Exception:
            return jsonify({"ok": False, "error": "invalid safety.forbidden_can_ids format"}), 400

        config.update(patch)
        log.info("Config updated via API: %s", list(patch.keys()))

        # Hot-apply CAN1 listen-only flag to the live CanManager
        if "can" in patch and "can1_listen_only" in patch["can"]:
            controller.can.set_can1_listen_only(bool(patch["can"]["can1_listen_only"]))

        return jsonify({"ok": True, "config": config.data})

    @app.route("/api/state", methods=["GET"])
    def api_state():
        return jsonify(_full_state())

    @app.route("/api/reboot", methods=["POST"])
    def api_reboot():
        import os
        os.system("sudo /sbin/reboot")
        return jsonify({"ok": True})

    # ---------------- OBD2 endpoints (Diagnostic mode only) ----------------

    def _reject_if_not_diagnostic():
        """Helper: returns a JSON error tuple if not in Diagnostic mode."""
        mode = config["can"].get("can1_mode", "pcm")
        if mode != "diagnostic":
            return jsonify({"ok": False,
                            "error": f"OBD2 tool only available in Diagnostic mode (current: {mode})"}), 400
        if config["can"].get("can1_listen_only", False):
            return jsonify({"ok": False,
                            "error": "CAN1 LISTEN-ONLY armed — disarm first to send UDS queries"}), 400
        return None

    @app.route("/api/obd2/read_map", methods=["POST"])
    def api_obd2_read_map():
        err = _reject_if_not_diagnostic()
        if err is not None:
            return err
        data = controller.get_uds_client().read_data_by_identifier(0x39C0, timeout_s=0.5)
        if data is None:
            return jsonify({"ok": False, "error": "no response (timeout or NRC)"}), 504
        mbar = decode_did_map_mbar(data)
        return jsonify({"ok": True, "map_mbar": mbar, "raw_hex": data.hex()})

    @app.route("/api/obd2/read_coolant", methods=["POST"])
    def api_obd2_read_coolant():
        err = _reject_if_not_diagnostic()
        if err is not None:
            return err
        data = controller.get_uds_client().read_data_by_identifier(0x202C, timeout_s=0.5)
        if data is None:
            return jsonify({"ok": False, "error": "no response (timeout or NRC)"}), 504
        c = decode_did_coolant_real_c(data)
        return jsonify({"ok": True, "coolant_real_c": c, "raw_hex": data.hex()})

    @app.route("/api/obd2/read_dtcs", methods=["POST"])
    def api_obd2_read_dtcs():
        err = _reject_if_not_diagnostic()
        if err is not None:
            return err
        dtcs = controller.get_uds_client().read_dtc_information(timeout_s=1.0)
        if dtcs is None:
            return jsonify({"ok": False, "error": "no response (timeout or NRC)"}), 504
        return jsonify({"ok": True, "count": len(dtcs),
                        "dtcs": [d.to_dict() for d in dtcs]})

    @app.route("/api/obd2/clear_dtcs", methods=["POST"])
    def api_obd2_clear_dtcs():
        err = _reject_if_not_diagnostic()
        if err is not None:
            return err
        ok = controller.get_uds_client().clear_diagnostic_information(timeout_s=2.0)
        return jsonify({"ok": ok})

    # Helper that merges per-iteration BoostState snapshot with CanManager safety counters
    def _full_state() -> dict:
        s = controller.state.snapshot()
        s["blocked_airbag"] = controller.can.blocked_forbidden_count
        s["blocked_listen_only"] = controller.can.blocked_listen_only_count
        s["blocked_pcm_mode"] = controller.can.blocked_pcm_mode_count
        return s

    # ---------------- WebSocket ----------------

    @socketio.on("connect")
    def on_connect():
        socketio.emit("config", config.data)
        socketio.emit("state", _full_state())

    # Background thread: push state every 200 ms
    def state_pusher():
        while True:
            socketio.emit("state", _full_state())
            time.sleep(0.2)

    threading.Thread(target=state_pusher, daemon=True).start()

    return app, socketio
