"""Donor blueprint: list food donations, manage pickup requests, track history."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from utils.db import fetch_all, fetch_one
from utils.security import role_required
from utils.uploads import save_food_image
from utils.validators import FOOD_CATEGORIES, FOOD_UNITS, validate_donation_form
from utils.workflow import (
    WorkflowError,
    advance_request_status,
    cancel_donation,
    create_donation,
    now_dt,
    respond_to_request,
    update_donation_details,
)

donor_bp = Blueprint("donor", __name__, url_prefix="/donor")


def _load_locations() -> tuple[list[dict], list[dict]]:
    districts = fetch_all("SELECT id, name FROM districts ORDER BY name ASC")
    areas = fetch_all("SELECT id, district_id, name FROM areas ORDER BY name ASC")
    return districts, areas


def _get_owned_donation_or_404(donation_id: int, donor_id: int) -> dict:
    donation = fetch_one(
        """
        SELECT d.*, dist.name AS district_name, area.name AS area_name
        FROM donations d
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        WHERE d.id = %s
        """,
        (donation_id,),
    )
    if not donation:
        abort(404)
    if donation["donor_id"] != donor_id:
        abort(403, description="You do not have permission to access this donation.")
    return donation


@donor_bp.route("/dashboard")
@role_required("donor")
def dashboard():
    donor_id = g.current_user["id"]
    donations = fetch_all(
        """
        SELECT d.*, dist.name AS district_name, area.name AS area_name
        FROM donations d
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        WHERE d.donor_id = %s
        ORDER BY d.created_at DESC, d.id DESC
        """,
        (donor_id,),
    )
    incoming_requests = fetch_all(
        """
        SELECT r.*, d.title AS donation_title, d.unit AS donation_unit,
               u.name AS ngo_contact, u.organization_name AS ngo_name, u.phone AS ngo_phone
        FROM food_requests r
        JOIN donations d ON d.id = r.donation_id
        JOIN users u ON u.id = r.ngo_id
        WHERE d.donor_id = %s AND r.status IN ('pending', 'approved', 'ready')
        ORDER BY r.created_at DESC, r.id DESC
        """,
        (donor_id,),
    )
    stats = fetch_one(
        """
        SELECT
            COUNT(*) AS total_donations,
            SUM(CASE WHEN status IN ('available', 'partially_reserved', 'reserved') THEN 1 ELSE 0 END) AS active_donations,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_donations
        FROM donations
        WHERE donor_id = %s
        """,
        (donor_id,),
    ) or {"total_donations": 0, "active_donations": 0, "completed_donations": 0}
    pending_count = sum(1 for r in incoming_requests if r["status"] == "pending")

    return render_template(
        "donor/dashboard.html",
        donations=donations,
        incoming_requests=incoming_requests,
        stats=stats,
        pending_count=pending_count,
    )


@donor_bp.route("/donations/new", methods=["GET", "POST"])
@role_required("donor")
def new_donation():
    user = g.current_user
    districts, areas = _load_locations()
    errors: dict[str, str] = {}
    form_data: dict = {
        "pickup_address": user.get("address", ""),
        "district_id": user.get("district_id") or "",
        "area_id": user.get("area_id") or "",
        "unit": "portions",
        "category": FOOD_CATEGORIES[0],
    }

    if request.method == "POST":
        form_data = dict(request.form)
        cleaned, errors = validate_donation_form(request.form, now=now_dt(), is_edit=False)
        image_path = ""
        if not errors and "image" in request.files:
            saved_path, upload_err = save_food_image(
                request.files.get("image"),
                current_app.config["UPLOAD_FOLDER"],
            )
            if upload_err:
                errors["image"] = upload_err
            elif saved_path:
                image_path = saved_path

        if not errors:
            try:
                donation_id = create_donation(user["id"], cleaned, image_path=image_path)
                flash("Donation listed and nearby approved NGOs have been notified.", "success")
                return redirect(url_for("donor.donation_detail", donation_id=donation_id))
            except WorkflowError as exc:
                errors["form"] = str(exc)

    return render_template(
        "donor/donation_form.html",
        mode="create",
        districts=districts,
        areas=areas,
        categories=FOOD_CATEGORIES,
        units=FOOD_UNITS,
        errors=errors,
        form_data=form_data,
        donation=None,
    )


@donor_bp.route("/donations/<int:donation_id>")
@role_required("donor")
def donation_detail(donation_id: int):
    donation = _get_owned_donation_or_404(donation_id, g.current_user["id"])
    requests_list = fetch_all(
        """
        SELECT r.*, u.name AS ngo_contact, u.organization_name AS ngo_name,
               u.phone AS ngo_phone, u.address AS ngo_address
        FROM food_requests r
        JOIN users u ON u.id = r.ngo_id
        WHERE r.donation_id = %s
        ORDER BY r.created_at DESC, r.id DESC
        """,
        (donation_id,),
    )
    history = fetch_all(
        """
        SELECT fc.*, u.name AS actor_name, u.role AS actor_role
        FROM food_collections fc
        LEFT JOIN users u ON u.id = fc.actor_id
        WHERE fc.donation_id = %s
        ORDER BY fc.created_at DESC, fc.id DESC
        """,
        (donation_id,),
    )
    return render_template(
        "donor/donation_detail.html",
        donation=donation,
        requests_list=requests_list,
        history=history,
    )


@donor_bp.route("/donations/<int:donation_id>/edit", methods=["GET", "POST"])
@role_required("donor")
def edit_donation(donation_id: int):
    user = g.current_user
    donation = _get_owned_donation_or_404(donation_id, user["id"])
    if donation["status"] in ("cancelled", "expired", "completed"):
        flash("Closed, cancelled, or expired donations cannot be edited.", "error")
        return redirect(url_for("donor.donation_detail", donation_id=donation_id))

    districts, areas = _load_locations()
    errors: dict[str, str] = {}
    form_data = dict(donation)
    for dt_field in ("prepared_at", "expires_at"):
        val = str(donation.get(dt_field) or "")
        if len(val) >= 16:
            form_data[dt_field] = val[:16].replace(" ", "T")

    if request.method == "POST":
        form_data = dict(request.form)
        cleaned, errors = validate_donation_form(request.form, now=now_dt(), is_edit=True)
        image_path = None
        if not errors and "image" in request.files:
            saved_path, upload_err = save_food_image(
                request.files.get("image"),
                current_app.config["UPLOAD_FOLDER"],
            )
            if upload_err:
                errors["image"] = upload_err
            elif saved_path:
                image_path = saved_path

        if not errors:
            try:
                update_donation_details(donation_id, user["id"], cleaned, image_path=image_path)
                flash("Donation details updated.", "success")
                return redirect(url_for("donor.donation_detail", donation_id=donation_id))
            except WorkflowError as exc:
                errors["form"] = str(exc)

    return render_template(
        "donor/donation_form.html",
        mode="edit",
        districts=districts,
        areas=areas,
        categories=FOOD_CATEGORIES,
        units=FOOD_UNITS,
        errors=errors,
        form_data=form_data,
        donation=donation,
    )


@donor_bp.route("/donations/<int:donation_id>/cancel", methods=["POST"])
@role_required("donor")
def cancel_donation_route(donation_id: int):
    _get_owned_donation_or_404(donation_id, g.current_user["id"])
    note = str(request.form.get("note") or "Cancelled by donor").strip()
    try:
        cancel_donation(donation_id, g.current_user["id"], note=note)
        flash("Donation cancelled.", "info")
    except WorkflowError as exc:
        flash(str(exc), "error")
    return redirect(url_for("donor.donation_detail", donation_id=donation_id))


def _handle_request_action(request_id: int, action: str):
    req_row = fetch_one("SELECT donation_id FROM food_requests WHERE id = %s", (request_id,))
    if not req_row:
        abort(404)
    donation = _get_owned_donation_or_404(req_row["donation_id"], g.current_user["id"])
    note = str(request.form.get("note") or "").strip()
    try:
        if action in ("approve", "reject"):
            respond_to_request(request_id, g.current_user["id"], action, donor_note=note)
            flash(f"Request {action}d.", "success" if action == "approve" else "info")
        else:
            advance_request_status(request_id, g.current_user["id"], "donor", action, note=note)
            flash(f"Request marked as {action}.", "success")
    except WorkflowError as exc:
        flash(str(exc), "error")
    return redirect(url_for("donor.donation_detail", donation_id=donation["id"]))


@donor_bp.route("/requests/<int:request_id>/approve", methods=["POST"])
@role_required("donor")
def approve_request(request_id: int):
    return _handle_request_action(request_id, "approve")


@donor_bp.route("/requests/<int:request_id>/reject", methods=["POST"])
@role_required("donor")
def reject_request(request_id: int):
    return _handle_request_action(request_id, "reject")


@donor_bp.route("/requests/<int:request_id>/ready", methods=["POST"])
@role_required("donor")
def mark_ready(request_id: int):
    return _handle_request_action(request_id, "ready")


@donor_bp.route("/requests/<int:request_id>/collected", methods=["POST"])
@role_required("donor")
def mark_collected(request_id: int):
    return _handle_request_action(request_id, "collected")
