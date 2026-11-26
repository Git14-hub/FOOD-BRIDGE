"""Admin blueprint: NGO approvals, user management, donation oversight, locations."""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from utils.db import execute, fetch_all, fetch_one
from utils.security import admin_required
from utils.validators import parse_int
from utils.workflow import WorkflowError, now_dt, set_ngo_approval

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    stats = fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM users WHERE role = 'donor') AS total_donors,
            (SELECT COUNT(*) FROM users WHERE role = 'ngo' AND ngo_status = 'approved') AS approved_ngos,
            (SELECT COUNT(*) FROM users WHERE role = 'ngo' AND ngo_status = 'pending') AS pending_ngos,
            (SELECT COUNT(*) FROM donations) AS total_donations,
            (SELECT COUNT(*) FROM food_requests WHERE status = 'distributed') AS distributed_requests
        """
    ) or {}
    pending_ngos = fetch_all(
        """
        SELECT u.*, d.name AS district_name, a.name AS area_name
        FROM users u
        LEFT JOIN districts d ON d.id = u.district_id
        LEFT JOIN areas a ON a.id = u.area_id
        WHERE u.role = 'ngo' AND u.ngo_status = 'pending'
        ORDER BY u.created_at ASC
        """
    )
    recent_history = fetch_all(
        """
        SELECT fc.*, don.title AS donation_title, u.name AS actor_name
        FROM food_collections fc
        JOIN donations don ON don.id = fc.donation_id
        LEFT JOIN users u ON u.id = fc.actor_id
        ORDER BY fc.created_at DESC, fc.id DESC
        LIMIT 12
        """
    )
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        pending_ngos=pending_ngos,
        recent_history=recent_history,
    )


@admin_bp.route("/ngos")
@admin_required
def ngos():
    status_filter = str(request.args.get("status") or "").strip()
    clauses = ["u.role = 'ngo'"]
    params: list = []
    if status_filter in ("pending", "approved", "rejected"):
        clauses.append("u.ngo_status = %s")
        params.append(status_filter)

    ngo_list = fetch_all(
        f"""
        SELECT u.*, d.name AS district_name, a.name AS area_name
        FROM users u
        LEFT JOIN districts d ON d.id = u.district_id
        LEFT JOIN areas a ON a.id = u.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY u.created_at DESC
        """,
        tuple(params),
    )
    return render_template("admin/ngos.html", ngo_list=ngo_list, status_filter=status_filter)


@admin_bp.route("/ngos/<int:ngo_id>/approve", methods=["POST"])
@admin_required
def approve_ngo(ngo_id: int):
    try:
        set_ngo_approval(ngo_id, g.current_user["id"], "approved")
        flash("NGO approved.", "success")
    except WorkflowError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.ngos"))


@admin_bp.route("/ngos/<int:ngo_id>/reject", methods=["POST"])
@admin_required
def reject_ngo(ngo_id: int):
    reason = str(request.form.get("reason") or "Verification criteria not met.").strip()
    try:
        set_ngo_approval(ngo_id, g.current_user["id"], "rejected", reason=reason)
        flash("NGO rejected.", "info")
    except WorkflowError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.ngos"))


@admin_bp.route("/users")
@admin_required
def users():
    users_list = fetch_all(
        """
        SELECT u.*, d.name AS district_name, a.name AS area_name
        FROM users u
        LEFT JOIN districts d ON d.id = u.district_id
        LEFT JOIN areas a ON a.id = u.area_id
        ORDER BY u.created_at DESC
        """
    )
    return render_template("admin/users.html", users_list=users_list)


@admin_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_user_active(user_id: int):
    if user_id == g.current_user["id"]:
        flash("You cannot deactivate your own admin account.", "error")
        return redirect(url_for("admin.users"))
    target = fetch_one("SELECT id, is_active FROM users WHERE id = %s", (user_id,))
    if not target:
        abort(404)
    new_state = 0 if target["is_active"] else 1
    ts = now_dt().strftime("%Y-%m-%d %H:%M:%S")
    execute(
        "UPDATE users SET is_active = %s, updated_at = %s WHERE id = %s",
        (new_state, ts, user_id),
    )
    flash("User status updated.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/donations")
@admin_required
def donations():
    all_donations = fetch_all(
        """
        SELECT d.*, u.name AS donor_name, dist.name AS district_name, area.name AS area_name
        FROM donations d
        JOIN users u ON u.id = d.donor_id
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        ORDER BY d.created_at DESC, d.id DESC
        """
    )
    return render_template("admin/donations.html", donations=all_donations)


@admin_bp.route("/locations", methods=["GET", "POST"])
@admin_required
def locations():
    if request.method == "POST":
        kind = request.form.get("kind")
        ts = now_dt().strftime("%Y-%m-%d %H:%M:%S")
        if kind == "district":
            name = str(request.form.get("name") or "").strip()
            if len(name) < 2 or len(name) > 120:
                flash("District name must be between 2 and 120 characters.", "error")
            else:
                execute(
                    "INSERT IGNORE INTO districts (name, created_at) VALUES (%s, %s)",
                    (name, ts),
                )
                flash(f"District '{name}' saved.", "success")
        elif kind == "area":
            district_id = parse_int(request.form.get("district_id"))
            name = str(request.form.get("name") or "").strip()
            if not district_id or len(name) < 2 or len(name) > 120:
                flash("Select a district and enter a valid area name.", "error")
            else:
                execute(
                    "INSERT IGNORE INTO areas (district_id, name, created_at) VALUES (%s, %s, %s)",
                    (district_id, name, ts),
                )
                flash(f"Area '{name}' saved.", "success")
        return redirect(url_for("admin.locations"))

    districts = fetch_all("SELECT id, name FROM districts ORDER BY name ASC")
    areas = fetch_all(
        """
        SELECT a.id, a.name, a.district_id, d.name AS district_name
        FROM areas a
        JOIN districts d ON d.id = a.district_id
        ORDER BY d.name ASC, a.name ASC
        """
    )
    return render_template("admin/locations.html", districts=districts, areas=areas)
