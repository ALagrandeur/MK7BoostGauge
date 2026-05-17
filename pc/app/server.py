"""PC-side Flask server.

Serves the web UI to the local browser (localhost:8080) and proxies
config push to the Pi via PiClient.

Endpoints:
  GET  /                  - serve UI (index.html)
  GET  /api/pc_config     - get current PC-side config (sliders state)
  POST /api/pc_config     - update PC-side config (save to pc_config.json)
  POST /api/send_to_pi    - push the current config to the Pi
  GET  /api/pi_ping       - check Pi reachability
  GET  /api/pi_status     - get Pi live status (for Transmission Input card)
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .config import PcConfig
from .pi_client import PiClient

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"


def create_app(config: PcConfig) -> Flask:
    # No static_url_path="" — we serve static files via explicit routes below
    # to avoid Flask's default static handler shadowing our '/' route.
    app = Flask(__name__, static_folder=None)

    # Build the Pi client lazily — host can change via UI
    def get_client() -> PiClient:
        return PiClient(host=config.data.get("pi_host", "boostgauge.local"),
                        port=int(config.data.get("pi_port", 8765)),
                        timeout=3.0)

    @app.route("/")
    def index():
        response = send_from_directory(STATIC_DIR, "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/<path:filename>")
    def static_files(filename):
        # Serve any other file (style.css, app.js, etc.) from static dir.
        # No-cache so JS/CSS updates are picked up immediately.
        response = send_from_directory(STATIC_DIR, filename)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/pc_config", methods=["GET"])
    def get_pc_config():
        response = jsonify(config.snapshot())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/pc_config", methods=["POST"])
    def update_pc_config():
        patch = request.get_json(force=True)
        if not isinstance(patch, dict):
            return jsonify({"ok": False, "error": "expect JSON object"}), 400
        ok, err = config.update(patch)
        if not ok:
            return jsonify({"ok": False, "error": f"PC save failed: {err}"}), 500
        return jsonify({"ok": True, "config": config.snapshot()})

    @app.route("/api/send_to_pi", methods=["POST"])
    def send_to_pi():
        """Push current PC config to the Pi (hot apply on Pi)."""
        # Optional: apply any incoming patch FIRST, then send everything
        patch = request.get_json(silent=True) or {}
        if isinstance(patch, dict) and patch:
            config.update(patch)

        client = get_client()
        ok, msg, pi_response = client.send_config(config.get_pi_config_only())
        return jsonify({
            "ok": ok,
            "message": msg,
            "pi_response": pi_response,
            "sent_config": config.get_pi_config_only(),
        }), (200 if ok else 502)

    @app.route("/api/pi_ping", methods=["GET"])
    def pi_ping():
        client = get_client()
        ok, msg = client.ping()
        return jsonify({"ok": ok, "message": msg, "host": client.base_url})

    @app.route("/api/pi_status", methods=["GET"])
    def pi_status():
        client = get_client()
        ok, data = client.get_status()
        if ok:
            return jsonify({"ok": True, "data": data})
        return jsonify({"ok": False, "data": None}), 502

    return app
