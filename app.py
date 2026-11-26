"""FoodBridge Flask application factory and entrypoint."""

from __future__ import annotations

import os

from flask import Flask, g, render_template, request

from config import Config
from modules.admin import admin_bp
from modules.auth import auth_bp
from modules.donor import donor_bp
from modules.ngo import ngo_bp
from modules.notifications import notifications_bp
from utils.db import fetch_one
from utils.security import generate_csrf_token, load_current_user, validate_csrf
from utils.workflow import expire_overdue_items


def create_app() -> Flask:
    """Create and configure the FoodBridge Flask application."""
    app = Flask(__name__)
    app.config.from_object(Config)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    app.register_blueprint(auth_bp)
    app.register_blueprint(donor_bp)
    app.register_blueprint(ngo_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(notifications_bp)

    @app.before_request
    def _before_request():
        if request.endpoint == "static":
            return
        validate_csrf()
        load_current_user()
        expire_overdue_items()

    @app.context_processor
    def _inject_template_context():
        user = getattr(g, "current_user", None)
        unread_count = 0
        if user:
            row = fetch_one(
                "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id = %s AND is_read = 0",
                (user["id"],),
            )
            unread_count = row["cnt"] if row else 0
        return {
            "csrf_token": generate_csrf_token,
            "current_user": user,
            "unread_notifications_count": unread_count,
        }

    @app.errorhandler(400)
    def handle_400(err):
        return render_template("errors/error.html", code=400, title="Bad Request", message=str(err.description)), 400

    @app.errorhandler(403)
    def handle_403(err):
        return render_template("errors/error.html", code=403, title="Access Denied", message=str(err.description)), 403

    @app.errorhandler(404)
    def handle_404(err):
        return render_template("errors/error.html", code=404, title="Page Not Found", message="The requested page could not be found."), 404

    @app.errorhandler(413)
    def handle_413(_err):
        return render_template("errors/error.html", code=413, title="File Too Large", message="Uploaded file exceeds the maximum allowed size."), 413

    @app.errorhandler(500)
    def handle_500(_err):
        return render_template("errors/error.html", code=500, title="Server Error", message="An unexpected error occurred. Please try again."), 500

    return app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)
