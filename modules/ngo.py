"""NGO blueprint: browse donations, submit partial requests, manage pickups."""

from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from utils.db import fetch_all, fetch_one
from utils.security import role_required
from utils.validators import FOOD_CATEGORIES, parse_int, validate_food_request_form
from utils.workflow import WorkflowError, advance_request_status, cancel_request, now_dt, request_food

ngo_bp = Blueprint("ngo", __name__, url_prefix="/ngo")


@ngo_bp.route("/dashboard")
@role_required("ngo")
def dashboard():
    user = g.current_user
    ngo_id = user["id"]
    now_str = now_dt().strftime("%Y-%m-%d %H:%M:%S")

    nearby_donations = fetch_all(
        """
        SELECT d.*, u.name AS donor_name, dist.name AS district_name, area.name AS area_name
        FROM donations d
        JOIN users u ON u.id = d.donor_id
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        WHERE d.status IN ('available', 'partially_reserved')
          AND d.expires_at > %s
          AND d.district_id = %s
          AND d.area_id = %s
        ORDER BY d.expires_at ASC
        LIMIT 6
        """,
        (now_str, user["district_id"], user["area_id"]),
    )
    my_requests = fetch_all(
        """
        SELECT r.*, d.title AS donation_title, d.unit AS donation_unit,
               d.pickup_address, d.expires_at, u.name AS donor_name, u.phone AS donor_phone
        FROM food_requests r
        JOIN donations d ON d.id = r.donation_id
        JOIN users u ON u.id = d.donor_id
        WHERE r.ngo_id = %s
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT 10
        """,
        (ngo_id,),
    )
    stats = fetch_one(
        """
        SELECT
            COUNT(*) AS total_requests,
            SUM(CASE WHEN status IN ('pending', 'approved', 'ready') THEN 1 ELSE 0 END) AS active_requests,
            SUM(CASE WHEN status = 'distributed' THEN 1 ELSE 0 END) AS distributed_count
        FROM food_requests
        WHERE ngo_id = %s
        """,
        (ngo_id,),
    ) or {"total_requests": 0, "active_requests": 0, "distributed_count": 0}

    return render_template(
        "ngo/dashboard.html",
        nearby_donations=nearby_donations,
        my_requests=my_requests,
        stats=stats,
    )


@ngo_bp.route("/browse")
@role_required("ngo")
def browse():
    user = g.current_user
    districts = fetch_all("SELECT id, name FROM districts ORDER BY name ASC")
    areas = fetch_all("SELECT id, district_id, name FROM areas ORDER BY name ASC")

    district_id = parse_int(request.args.get("district_id"))
    area_id = parse_int(request.args.get("area_id"))
    if "district_id" not in request.args and user.get("district_id"):
        district_id = user["district_id"]
        area_id = user.get("area_id")

    category = str(request.args.get("category") or "").strip()
    search_q = str(request.args.get("q") or "").strip()

    now_str = now_dt().strftime("%Y-%m-%d %H:%M:%S")
    clauses = [
        "d.status IN ('available', 'partially_reserved')",
        "d.expires_at > %s",
    ]
    params: list = [now_str]

    if district_id:
        clauses.append("d.district_id = %s")
        params.append(district_id)
    if area_id:
        clauses.append("d.area_id = %s")
        params.append(area_id)
    if category and category in FOOD_CATEGORIES:
        clauses.append("d.category = %s")
        params.append(category)
    if search_q:
        clauses.append("(d.title LIKE %s OR d.description LIKE %s)")
        like = f"%{search_q}%"
        params.extend([like, like])

    sql = f"""
        SELECT d.*, u.name AS donor_name, dist.name AS district_name, area.name AS area_name
        FROM donations d
        JOIN users u ON u.id = d.donor_id
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY d.expires_at ASC, d.id DESC
    """
    donations = fetch_all(sql, tuple(params))

    return render_template(
        "ngo/browse.html",
        donations=donations,
        districts=districts,
        areas=areas,
        categories=FOOD_CATEGORIES,
        selected_district=district_id,
        selected_area=area_id,
        selected_category=category,
        search_q=search_q,
    )


@ngo_bp.route("/donations/<int:donation_id>")
@role_required("ngo")
def donation_detail(donation_id: int):
    donation = fetch_one(
        """
        SELECT d.*, u.name AS donor_name, u.phone AS donor_phone, u.organization_name AS donor_org,
               dist.name AS district_name, area.name AS area_name
        FROM donations d
        JOIN users u ON u.id = d.donor_id
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        WHERE d.id = %s
        """,
        (donation_id,),
    )
    if not donation:
        abort(404)

    my_requests = fetch_all(
        """
        SELECT * FROM food_requests
        WHERE donation_id = %s AND ngo_id = %s
        ORDER BY created_at DESC, id DESC
        """,
        (donation_id, g.current_user["id"]),
    )
    return render_template(
        "ngo/donation_detail.html",
        donation=donation,
        my_requests=my_requests,
        errors={},
        form_data={},
    )


@ngo_bp.route("/donations/<int:donation_id>/request", methods=["POST"])
@role_required("ngo")
def submit_request(donation_id: int):
    user = g.current_user
    if user["ngo_status"] != "approved":
        flash("Your NGO account must be approved by an admin before requesting food.", "error")
        return redirect(url_for("ngo.donation_detail", donation_id=donation_id))

    donation = fetch_one(
        """
        SELECT d.*, u.name AS donor_name, u.phone AS donor_phone, u.organization_name AS donor_org,
               dist.name AS district_name, area.name AS area_name
        FROM donations d
        JOIN users u ON u.id = d.donor_id
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        WHERE d.id = %s
        """,
        (donation_id,),
    )
    if not donation:
        abort(404)

    cleaned, errors = validate_food_request_form(request.form)
    if not errors:
        try:
            request_food(
                donation_id=donation_id,
                ngo_id=user["id"],
                requested_qty=cleaned["requested_quantity"],
                ngo_note=cleaned["ngo_note"],
            )
            flash("Food request submitted and quantity reserved pending donor approval.", "success")
            return redirect(url_for("ngo.requests_list"))
        except WorkflowError as exc:
            errors["form"] = str(exc)

    my_requests = fetch_all(
        "SELECT * FROM food_requests WHERE donation_id = %s AND ngo_id = %s ORDER BY created_at DESC",
        (donation_id, user["id"]),
    )
    return render_template(
        "ngo/donation_detail.html",
        donation=donation,
        my_requests=my_requests,
        errors=errors,
        form_data=dict(request.form),
    ), 400


@ngo_bp.route("/requests")
@role_required("ngo")
def requests_list():
    status_filter = str(request.args.get("status") or "").strip()
    clauses = ["r.ngo_id = %s"]
    params: list = [g.current_user["id"]]
    if status_filter:
        clauses.append("r.status = %s")
        params.append(status_filter)

    items = fetch_all(
        f"""
        SELECT r.*, d.title AS donation_title, d.unit AS donation_unit,
               d.pickup_address, d.expires_at, d.status AS donation_status,
               u.name AS donor_name, u.phone AS donor_phone,
               dist.name AS district_name, area.name AS area_name
        FROM food_requests r
        JOIN donations d ON d.id = r.donation_id
        JOIN users u ON u.id = d.donor_id
        JOIN districts dist ON dist.id = d.district_id
        JOIN areas area ON area.id = d.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY r.created_at DESC, r.id DESC
        """,
        tuple(params),
    )
    return render_template("ngo/requests.html", requests_list=items, status_filter=status_filter)


def _check_owned_request_or_404(request_id: int, ngo_id: int) -> dict:
    req_row = fetch_one("SELECT * FROM food_requests WHERE id = %s", (request_id,))
    if not req_row:
        abort(404)
    if req_row["ngo_id"] != ngo_id:
        abort(403, description="You do not own this food request.")
    return req_row


@ngo_bp.route("/requests/<int:request_id>/cancel", methods=["POST"])
@role_required("ngo")
def cancel_my_request(request_id: int):
    _check_owned_request_or_404(request_id, g.current_user["id"])
    note = str(request.form.get("note") or "Cancelled by NGO").strip()
    try:
        cancel_request(request_id, g.current_user["id"], note=note)
        flash("Request cancelled and quantity released.", "info")
    except WorkflowError as exc:
        flash(str(exc), "error")
    return redirect(url_for("ngo.requests_list"))


@ngo_bp.route("/requests/<int:request_id>/collected", methods=["POST"])
@role_required("ngo")
def mark_collected(request_id: int):
    _check_owned_request_or_404(request_id, g.current_user["id"])
    note = str(request.form.get("note") or "Collected by NGO").strip()
    try:
        advance_request_status(request_id, g.current_user["id"], "ngo", "collected", note=note)
        flash("Marked as collected.", "success")
    except WorkflowError as exc:
        flash(str(exc), "error")
    return redirect(url_for("ngo.requests_list"))


@ngo_bp.route("/requests/<int:request_id>/distributed", methods=["POST"])
@role_required("ngo")
def mark_distributed(request_id: int):
    _check_owned_request_or_404(request_id, g.current_user["id"])
    note = str(request.form.get("note") or "Distributed to beneficiaries").strip()
    try:
        advance_request_status(request_id, g.current_user["id"], "ngo", "distributed", note=note)
        flash("Marked as distributed. Thank you!", "success")
    except WorkflowError as exc:
        flash(str(exc), "error")
    return redirect(url_for("ngo.requests_list"))
