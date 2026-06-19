"""Battery lab automation core package."""

__version__ = "0.1.3"

__all__ = [
    "file_io",
    "metrics",
    "plots",
    "report",
    "insights",
    "register_battery_lab",
]


def register_battery_lab(app):
    from . import job_models  # noqa: F401
    from .routes import blueprint

    app.config.setdefault("BATTERY_LAB_LAYOUT_TEMPLATE", "battery_lab/standalone.html")
    if "battery_lab" not in app.blueprints:
        app.register_blueprint(blueprint)
    app.config["BATTERY_LAB_ENABLED"] = True
    return app
