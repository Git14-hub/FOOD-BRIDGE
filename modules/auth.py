"""Authentication, landing page, and profile blueprint."""

from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from utils.db import execute, fetch_all, fetch_one
from utils.security import hash_password, login_required, verify_password
from utils.validators import validate_profile_update, validate_registration
from utils.workflow import now_dt

auth_bp = Blueprint("auth", __name__)


def _dashboard_url_for_role(role: str) -> str:
    if role == "admin":
        return url_for("admin.dashboard")
    if role == "ngo":
        return url_for("ngo.dashboard")
    return url_for("donor.dashboard")


def _load_locations() -> tuple[list[dict], list[dict]]:
    districts = fetch_all("SELECT id, name FROM districts ORDER BY name ASC")
    areas = fetch_all("SELECT id, district_id, name FROM areas ORDER BY name ASC")
    return districts, areas


@auth_bp.route("/")
def index():
    stats = fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM donations) AS total_donations,
            (SELECT COUNT(*) FROM users WHERE role = 'ngo' AND ngo_status = 'approved') AS approved_ngos,
            (SELECT COUNT(*) FROM food_requests WHERE status IN ('collected', 'distributed')) AS completed_pickups
        """
    ) or {"total_donations": 0, "approved_ngos": 0, "completed_pickups": 0}
    return render_template("index.html", stats=stats)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if g.current_user:
        return redirect(_dashboard_url_for_role(g.current_user["role"]))

    districts, areas = _load_locations()
    errors: dict[str, str] = {}
    form_data: dict = dict(request.form) if request.method == "POST" else {"role": "donor"}

    if request.method == "POST":
        cleaned, errors = validate_registration(request.form)
        if not errors:
            existing = fetch_one("SELECT id FROM users WHERE email = %s", (cleaned["email"],))
            if existing:
                errors["email"] = "An account with this email already exists."

        if not errors:
            ts = now_dt().strftime("%Y-%m-%d %H:%M:%S")
            ngo_status = "pending" if cleaned["role"] == "ngo" else "not_applicable"
            user_id = execute(
                """
                INSERT INTO users (
                    name, email, password_hash, role, phone,
                    organization_name, registration_number, address,
                    district_id, area_id, ngo_status, rejection_reason,
                    is_active, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', 1, %s, %s)
                """,
                (
                    cleaned["name"],
                    cleaned["email"],
                    hash_password(cleaned["password"]),
                    cleaned["role"],
                    cleaned["phone"],
                    cleaned["organization_name"],
                    cleaned["registration_number"],
                    cleaned["address"],
                    cleaned["district_id"],
                    cleaned["area_id"],
                    ngo_status,
                    ts,
                    ts,
                ),
            )
            session.clear()
            session["user_id"] = user_id
            if cleaned["role"] == "ngo":
                flash(
                    "NGO account registered. An administrator will review and approve your organization before you can request food.",
                    "info",
                )
            else:
                flash("Welcome to FoodBridge! Your donor account is ready.", "success")
            return redirect(_dashboard_url_for_role(cleaned["role"]))

    return render_template(
        "auth/register.html",
        districts=districts,
        areas=areas,
        errors=errors,
        form_data=form_data,
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.current_user:
        return redirect(_dashboard_url_for_role(g.current_user["role"]))

    errors: dict[str, str] = {}
    email = ""
    if request.method == "POST":
        email = str(request.form.get("email") or "").strip().lower()
        password = str(request.form.get("password") or "")
        if not email or not password:
            errors["form"] = "Please enter both email and password."
        else:
            user = fetch_one("SELECT * FROM users WHERE email = %s", (email,))
            if not user or not verify_password(user["password_hash"], password):
                errors["form"] = "Invalid email or password."
            elif not user["is_active"]:
                errors["form"] = "This account has been deactivated. Contact an administrator."
            else:
                session.clear()
                session["user_id"] = user["id"]
                flash(f"Signed in as {user['name']}.", "success")
                next_url = request.args.get("next", "")
                if next_url.startswith("/") and not next_url.startswith("//"):
                    return redirect(next_url)
                return redirect(_dashboard_url_for_role(user["role"]))

    return render_template("auth/login.html", errors=errors, email=email)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = g.current_user
    districts, areas = _load_locations()
    errors: dict[str, str] = {}
    form_data = dict(user)

    if request.method == "POST":
        action = request.form.get("action", "update_profile")
        if action == "change_password":
            current_pw = str(request.form.get("current_password") or "")
            new_pw = str(request.form.get("new_password") or "")
            confirm_pw = str(request.form.get("confirm_password") or "")
            if not verify_password(user["password_hash"], current_pw):
                errors["current_password"] = "Current password is incorrect."
            elif len(new_pw) < 8:
                errors["new_password"] = "New password must be at least 8 characters."
            elif new_pw != confirm_pw:
                errors["confirm_password"] = "New passwords do not match."
            if not errors:
                ts = now_dt().strftime("%Y-%m-%d %H:%M:%S")
                execute(
                    "UPDATE users SET password_hash = %s, updated_at = %s WHERE id = %s",
                    (hash_password(new_pw), ts, user["id"]),
                )
                flash("Password updated successfully.", "success")
                return redirect(url_for("auth.profile"))
        else:
            form_data = dict(request.form)
            cleaned, errors = validate_profile_update(request.form, user["role"])
            if not errors:
                ts = now_dt().strftime("%Y-%m-%d %H:%M:%S")
                execute(
                    """
                    UPDATE users
                    SET name = %s, phone = %s, address = %s, organization_name = %s,
                        district_id = %s, area_id = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (
                        cleaned["name"],
                        cleaned["phone"],
                        cleaned["address"],
                        cleaned["organization_name"],
                        cleaned["district_id"],
                        cleaned["area_id"],
                        ts,
                        user["id"],
                    ),
                )
                flash("Profile updated.", "success")
                return redirect(url_for("auth.profile"))

    return render_template(
        "auth/profile.html",
        districts=districts,
        areas=areas,
        errors=errors,
        form_data=form_data,
    )
