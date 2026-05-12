"""Flask + SocketIO web UI — config + live telemetry."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO

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
        config.update(patch)
        log.info("Config updated via API: %s", list(patch.keys()))
        return jsonify({"ok": True, "config": config.data})

    @app.route("/api/state", methods=["GET"])
    def api_state():
        return jsonify(controller.state.snapshot())

    @app.route("/api/reboot", methods=["POST"])
    def api_reboot():
        # Soft option: only if explicitly enabled
        import os
        os.system("sudo /sbin/reboot")
        return jsonify({"ok": True})

    # ---------------- WebSocket ----------------

    @socketio.on("connect")
    def on_connect():
        socketio.emit("config", config.data)
        socketio.emit("state", controller.state.snapshot())

    # Background thread: push state every 200 ms
    def state_pusher():
        while True:
            socketio.emit("state", controller.state.snapshot())
            time.sleep(0.2)

    threading.Thread(target=state_pusher, daemon=True).start()

    return app, socketio
