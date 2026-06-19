from __future__ import annotations

import os
from typing import Any

from flask import Flask, redirect, url_for

from . import register_battery_lab


def create_app(config: dict[str, Any] | None = None) -> Flask:
    """Create a standalone Flask shell for the packaged Battery Lab app."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("BATTERY_SECRET_KEY") or os.getenv("SECRET_KEY") or "battery-lab-local-dev"
    if config:
        app.config.update(config)
    register_battery_lab(app)

    @app.route("/")
    def battery_lab_index_redirect() -> Any:
        return redirect(url_for("battery_lab.index"))

    return app


app = create_app()
