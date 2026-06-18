"""Battery lab automation core package."""

__version__ = "0.1.0"

__all__ = [
    "file_io",
    "metrics",
    "plots",
    "report",
    "insights",
    "register_battery_lab",
]


def register_battery_lab(app):
    from .routes import blueprint

    if "battery_lab" not in app.blueprints:
        app.register_blueprint(blueprint)
    app.config["BATTERY_LAB_ENABLED"] = True
    return app
