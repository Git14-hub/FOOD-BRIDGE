"""Notifications and location lookup API blueprint."""

from __future__ import annotations

from flask import Blueprint, flash, g, jsonify, redirect, render_template, url_for

from utils.db import execute, fetch_all, fetch_one
from utils.security import login_required

notifications_bp = Blueprint("notifications", __name__)


@notifications_bp.route("/notifications")
@login_required
def list_notifications():
    items = fetch_all(
        """
        SELECT * FROM notifications
        WHERE user_id = %s
        ORDER BY created_at DESC, id DESC
        """,
        (g.current_user["id"],),
    )
    return render_template("notifications.html", notifications=items)


@notifications_bp.route("/notifications/<int:notif_id>/read", methods=["POST"])
@login_required
def mark_read(notif_id: int):
    row = fetch_one(
        "SELECT id, user_id, link FROM notifications WHERE id = %s",
        (notif_id,),
    )
    if row and row["user_id"] == g.current_user["id"]:
        execute("UPDATE notifications SET is_read = 1 WHERE id = %s", (notif_id,))
        if row.get("link") and str(row["link"]).startswith("/"):
            return redirect(row["link"])
    return redirect(url_for("notifications.list_notifications"))


@notifications_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_read():
    execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s", (g.current_user["id"],))
    flash("All notifications marked as read.", "info")
    return redirect(url_for("notifications.list_notifications"))


@notifications_bp.route("/api/areas/<int:district_id>")
def areas_by_district(district_id: int):
    areas = fetch_all(
        "SELECT id, name FROM areas WHERE district_id = %s ORDER BY name ASC",
        (district_id,),
    )
    return jsonify({"areas": areas})
