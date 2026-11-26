"""Core FoodBridge business rules and state transitions.

All changes to `available_quantity`, donation status, and request status must happen
exclusively through functions in this module inside `transaction()` using row locks
(`FOR UPDATE` in lock order: donation first, then request).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from utils.db import fetch_all, fetch_one, transaction


class WorkflowError(Exception):
    """Domain rule violation raised by workflow operations."""


def now_dt() -> datetime:
    """Return current local naive datetime truncated to seconds."""
    return datetime.now().replace(microsecond=0)


def _dt_str(dt: datetime | str | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _to_decimal(val: Any) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"))


def _compute_donation_status(
    total_qty: Decimal,
    available_qty: Decimal,
    current_status: str,
) -> str:
    """Determine donation status from remaining quantity."""
    if current_status in ("cancelled", "expired", "completed"):
        return current_status
    if available_qty <= Decimal("0"):
        return "reserved"
    if available_qty < total_qty:
        return "partially_reserved"
    return "available"


def log_history(
    cur: Any,
    donation_id: int,
    request_id: int | None,
    actor_id: int | None,
    from_status: str,
    to_status: str,
    quantity: Decimal | float | int = 0,
    note: str = "",
    created_at: datetime | None = None,
) -> None:
    """Insert an audit trail entry into `food_collections`."""
    ts = _dt_str(created_at or now_dt())
    cur.execute(
        """
        INSERT INTO food_collections (
            donation_id, request_id, actor_id, from_status, to_status, quantity, note, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            donation_id,
            request_id,
            actor_id,
            from_status,
            to_status,
            str(_to_decimal(quantity)),
            (note or "")[:255],
            ts,
        ),
    )


def notify(
    cur: Any,
    user_id: int,
    title: str,
    message: str,
    link: str = "",
    donation_id: int | None = None,
    request_id: int | None = None,
    created_at: datetime | None = None,
) -> None:
    """Create a notification row for a user inside an active cursor/transaction."""
    ts = _dt_str(created_at or now_dt())
    cur.execute(
        """
        INSERT INTO notifications (
            user_id, donation_id, request_id, title, message, link, is_read, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
        """,
        (
            user_id,
            donation_id,
            request_id,
            title[:160],
            message[:500],
            link[:255],
            ts,
        ),
    )


def create_donation(donor_id: int, data: dict[str, Any], image_path: str = "") -> int:
    """Create a new food donation and notify approved NGOs in the same district+area."""
    now = now_dt()
    ts = _dt_str(now)
    qty = _to_decimal(data["quantity"])

    with transaction() as (_conn, cur):
        donor = cur.execute(
            "SELECT id, name, role, is_active FROM users WHERE id = %s",
            (donor_id,),
        )
        _ = donor
        donor_row = cur.fetchone()
        if not donor_row or donor_row["role"] != "donor" or not donor_row["is_active"]:
            raise WorkflowError("Only active donors can list food donations.")

        cur.execute(
            """
            INSERT INTO donations (
                donor_id, title, category, description,
                total_quantity, available_quantity, unit,
                pickup_address, district_id, area_id,
                prepared_at, expires_at, image_path, status,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'available', %s, %s)
            """,
            (
                donor_id,
                data["title"],
                data["category"],
                data.get("description", ""),
                str(qty),
                str(qty),
                data["unit"],
                data["pickup_address"],
                data["district_id"],
                data["area_id"],
                _dt_str(data.get("prepared_at")),
                _dt_str(data["expires_at"]),
                image_path or "",
                ts,
                ts,
            ),
        )
        donation_id = cur.lastrowid

        log_history(
            cur,
            donation_id=donation_id,
            request_id=None,
            actor_id=donor_id,
            from_status="",
            to_status="available",
            quantity=qty,
            note=f"Donation listed: {data['title']}",
            created_at=now,
        )

        # Rule 6: Notify ONLY Approved NGOs with the exact same district_id and area_id.
        cur.execute(
            """
            SELECT id FROM users
            WHERE role = 'ngo'
              AND ngo_status = 'approved'
              AND is_active = 1
              AND district_id = %s
              AND area_id = %s
            """,
            (data["district_id"], data["area_id"]),
        )
        ngos = cur.fetchall()
        for ngo in ngos:
            notify(
                cur,
                user_id=ngo["id"],
                title="New food available in your area",
                message=f"{data['title']} ({qty} {data['unit']}) listed by {donor_row['name']}.",
                link=f"/ngo/donations/{donation_id}",
                donation_id=donation_id,
                created_at=now,
            )

    return donation_id


def update_donation_details(donation_id: int, donor_id: int, data: dict[str, Any], image_path: str | None = None) -> None:
    """Edit non-quantity metadata of a donor's own active donation."""
    now = now_dt()
    ts = _dt_str(now)
    with transaction() as (_conn, cur):
        cur.execute("SELECT * FROM donations WHERE id = %s FOR UPDATE", (donation_id,))
        donation = cur.fetchone()
        if not donation:
            raise WorkflowError("Donation not found.")
        if donation["donor_id"] != donor_id:
            raise WorkflowError("You do not own this donation.")
        if donation["status"] in ("cancelled", "expired", "completed"):
            raise WorkflowError("Cannot edit a closed, cancelled, or expired donation.")

        final_image = image_path if image_path is not None else donation["image_path"]
        cur.execute(
            """
            UPDATE donations
            SET title = %s, category = %s, description = %s,
                pickup_address = %s, district_id = %s, area_id = %s,
                prepared_at = %s, expires_at = %s, image_path = %s, updated_at = %s
            WHERE id = %s
            """,
            (
                data["title"],
                data["category"],
                data.get("description", ""),
                data["pickup_address"],
                data["district_id"],
                data["area_id"],
                _dt_str(data.get("prepared_at")),
                _dt_str(data["expires_at"]),
                final_image,
                ts,
                donation_id,
            ),
        )


def cancel_donation(donation_id: int, donor_id: int, note: str = "Cancelled by donor") -> None:
    """Cancel a donation owned by donor_id and cancel all open requests."""
    now = now_dt()
    ts = _dt_str(now)
    with transaction() as (_conn, cur):
        cur.execute("SELECT * FROM donations WHERE id = %s FOR UPDATE", (donation_id,))
        donation = cur.fetchone()
        if not donation:
            raise WorkflowError("Donation not found.")
        if donation["donor_id"] != donor_id:
            raise WorkflowError("You do not own this donation.")
        if donation["status"] in ("cancelled", "expired", "completed"):
            raise WorkflowError("This donation cannot be cancelled.")

        cur.execute(
            """
            SELECT * FROM food_requests
            WHERE donation_id = %s AND status IN ('pending', 'approved', 'ready')
            FOR UPDATE
            """,
            (donation_id,),
        )
        open_requests = cur.fetchall()
        released_total = Decimal("0.00")
        for req in open_requests:
            req_qty = _to_decimal(req["requested_quantity"])
            released_total += req_qty
            old_req_status = req["status"]
            cur.execute(
                "UPDATE food_requests SET status = 'cancelled', updated_at = %s WHERE id = %s",
                (ts, req["id"]),
            )
            log_history(
                cur,
                donation_id=donation_id,
                request_id=req["id"],
                actor_id=donor_id,
                from_status=old_req_status,
                to_status="cancelled",
                quantity=req_qty,
                note=note,
                created_at=now,
            )
            notify(
                cur,
                user_id=req["ngo_id"],
                title="Donation cancelled by donor",
                message=f"Donation '{donation['title']}' was cancelled by the donor.",
                link="/ngo/requests",
                donation_id=donation_id,
                request_id=req["id"],
                created_at=now,
            )

        new_avail = _to_decimal(donation["available_quantity"]) + released_total
        old_don_status = donation["status"]
        cur.execute(
            "UPDATE donations SET available_quantity = %s, status = 'cancelled', updated_at = %s WHERE id = %s",
            (str(new_avail), ts, donation_id),
        )
        log_history(
            cur,
            donation_id=donation_id,
            request_id=None,
            actor_id=donor_id,
            from_status=old_don_status,
            to_status="cancelled",
            quantity=new_avail,
            note=note,
            created_at=now,
        )
        notify(
            cur,
            user_id=donor_id,
            title="Donation cancelled",
            message=f"Your donation '{donation['title']}' has been cancelled.",
            link=f"/donor/donations/{donation_id}",
            donation_id=donation_id,
            created_at=now,
        )


def request_food(donation_id: int, ngo_id: int, requested_qty: Decimal, ngo_note: str = "") -> int:
    """Reserve a partial or full quantity for an approved NGO inside a transaction."""
    now = now_dt()
    ts = _dt_str(now)
    qty = _to_decimal(requested_qty)
    if qty <= Decimal("0"):
        raise WorkflowError("Requested quantity must be positive.")

    with transaction() as (_conn, cur):
        # Lock donation row first
        cur.execute("SELECT * FROM donations WHERE id = %s FOR UPDATE", (donation_id,))
        donation = cur.fetchone()
        if not donation:
            raise WorkflowError("Donation not found.")

        cur.execute("SELECT * FROM users WHERE id = %s", (ngo_id,))
        ngo = cur.fetchone()
        if not ngo or ngo["role"] != "ngo" or not ngo["is_active"]:
            raise WorkflowError("Only active NGOs can request food.")
        if ngo["ngo_status"] != "approved":
            raise WorkflowError("Your NGO account is pending admin approval and cannot request food yet.")

        if donation["status"] not in ("available", "partially_reserved"):
            raise WorkflowError("This donation is no longer available for requests.")

        if str(donation["expires_at"]) <= ts:
            raise WorkflowError("This donation has already expired.")

        avail = _to_decimal(donation["available_quantity"])
        if qty > avail:
            raise WorkflowError(f"Requested quantity ({qty}) exceeds available quantity ({avail}).")

        new_avail = (avail - qty).quantize(Decimal("0.01"))
        new_don_status = _compute_donation_status(
            _to_decimal(donation["total_quantity"]),
            new_avail,
            donation["status"],
        )

        cur.execute(
            "UPDATE donations SET available_quantity = %s, status = %s, updated_at = %s WHERE id = %s",
            (str(new_avail), new_don_status, ts, donation_id),
        )
        cur.execute(
            """
            INSERT INTO food_requests (
                donation_id, ngo_id, requested_quantity, status, ngo_note, donor_note, created_at, updated_at
            ) VALUES (%s, %s, %s, 'pending', %s, '', %s, %s)
            """,
            (donation_id, ngo_id, str(qty), ngo_note[:255], ts, ts),
        )
        request_id = cur.lastrowid

        log_history(
            cur,
            donation_id=donation_id,
            request_id=request_id,
            actor_id=ngo_id,
            from_status="",
            to_status="pending",
            quantity=qty,
            note=ngo_note or "NGO requested food",
            created_at=now,
        )

        ngo_label = ngo["organization_name"] or ngo["name"]
        notify(
            cur,
            user_id=donation["donor_id"],
            title="New food request received",
            message=f"{ngo_label} requested {qty} {donation['unit']} of '{donation['title']}'.",
            link=f"/donor/donations/{donation_id}",
            donation_id=donation_id,
            request_id=request_id,
            created_at=now,
        )

    return request_id


def respond_to_request(request_id: int, donor_id: int, decision: str, donor_note: str = "") -> None:
    """Donor approves or rejects a pending request (lock order: donation first, then request)."""
    if decision not in ("approve", "reject"):
        raise WorkflowError("Invalid decision.")

    now = now_dt()
    ts = _dt_str(now)
    req_meta = fetch_one("SELECT donation_id FROM food_requests WHERE id = %s", (request_id,))
    if not req_meta:
        raise WorkflowError("Food request not found.")
    donation_id = req_meta["donation_id"]

    with transaction() as (_conn, cur):
        # Lock order: donation first, then request
        cur.execute("SELECT * FROM donations WHERE id = %s FOR UPDATE", (donation_id,))
        donation = cur.fetchone()
        cur.execute("SELECT * FROM food_requests WHERE id = %s FOR UPDATE", (request_id,))
        req = cur.fetchone()

        if not donation or not req or req["donation_id"] != donation["id"]:
            raise WorkflowError("Request or donation not found.")
        if donation["donor_id"] != donor_id:
            raise WorkflowError("You do not have permission to respond to this request.")
        if req["status"] != "pending":
            raise WorkflowError("Only pending requests can be approved or rejected.")

        if decision == "approve" and str(donation["expires_at"]) <= ts:
            raise WorkflowError("Cannot approve a request for an expired donation.")

        qty = _to_decimal(req["requested_quantity"])
        if decision == "approve":
            new_status = "approved"
            cur.execute(
                "UPDATE food_requests SET status = %s, donor_note = %s, updated_at = %s WHERE id = %s",
                (new_status, donor_note[:255], ts, request_id),
            )
            msg = f"Your request for {qty} {donation['unit']} of '{donation['title']}' was approved."
            title = "Food request approved"
        else:
            new_status = "rejected"
            avail = _to_decimal(donation["available_quantity"])
            new_avail = (avail + qty).quantize(Decimal("0.01"))
            new_don_status = _compute_donation_status(
                _to_decimal(donation["total_quantity"]),
                new_avail,
                donation["status"],
            )
            cur.execute(
                "UPDATE donations SET available_quantity = %s, status = %s, updated_at = %s WHERE id = %s",
                (str(new_avail), new_don_status, ts, donation_id),
            )
            cur.execute(
                "UPDATE food_requests SET status = %s, donor_note = %s, updated_at = %s WHERE id = %s",
                (new_status, donor_note[:255], ts, request_id),
            )
            msg = f"Your request for {qty} {donation['unit']} of '{donation['title']}' was rejected."
            title = "Food request rejected"

        log_history(
            cur,
            donation_id=donation_id,
            request_id=request_id,
            actor_id=donor_id,
            from_status="pending",
            to_status=new_status,
            quantity=qty,
            note=donor_note or f"Request {new_status} by donor",
            created_at=now,
        )
        notify(
            cur,
            user_id=req["ngo_id"],
            title=title,
            message=msg,
            link="/ngo/requests",
            donation_id=donation_id,
            request_id=request_id,
            created_at=now,
        )


def cancel_request(request_id: int, ngo_id: int, note: str = "Cancelled by NGO") -> None:
    """NGO cancels its own pending/approved/ready request, releasing reserved quantity."""
    now = now_dt()
    ts = _dt_str(now)
    req_meta = fetch_one("SELECT donation_id FROM food_requests WHERE id = %s", (request_id,))
    if not req_meta:
        raise WorkflowError("Food request not found.")
    donation_id = req_meta["donation_id"]

    with transaction() as (_conn, cur):
        # Lock order: donation first, then request
        cur.execute("SELECT * FROM donations WHERE id = %s FOR UPDATE", (donation_id,))
        donation = cur.fetchone()
        cur.execute("SELECT * FROM food_requests WHERE id = %s FOR UPDATE", (request_id,))
        req = cur.fetchone()

        if not donation or not req or req["donation_id"] != donation["id"]:
            raise WorkflowError("Request or donation not found.")
        if req["ngo_id"] != ngo_id:
            raise WorkflowError("You do not own this request.")
        if req["status"] not in ("pending", "approved", "ready"):
            raise WorkflowError("This request can no longer be cancelled.")

        old_status = req["status"]
        qty = _to_decimal(req["requested_quantity"])
        avail = _to_decimal(donation["available_quantity"])
        new_avail = (avail + qty).quantize(Decimal("0.01"))
        new_don_status = _compute_donation_status(
            _to_decimal(donation["total_quantity"]),
            new_avail,
            donation["status"],
        )

        cur.execute(
            "UPDATE donations SET available_quantity = %s, status = %s, updated_at = %s WHERE id = %s",
            (str(new_avail), new_don_status, ts, donation_id),
        )
        cur.execute(
            "UPDATE food_requests SET status = 'cancelled', updated_at = %s WHERE id = %s",
            (ts, request_id),
        )

        log_history(
            cur,
            donation_id=donation_id,
            request_id=request_id,
            actor_id=ngo_id,
            from_status=old_status,
            to_status="cancelled",
            quantity=qty,
            note=note,
            created_at=now,
        )
        notify(
            cur,
            user_id=donation["donor_id"],
            title="Food request cancelled",
            message=f"An NGO cancelled its request for {qty} {donation['unit']} of '{donation['title']}'.",
            link=f"/donor/donations/{donation_id}",
            donation_id=donation_id,
            request_id=request_id,
            created_at=now,
        )


def advance_request_status(
    request_id: int,
    actor_id: int,
    actor_role: str,
    target_status: str,
    note: str = "",
) -> None:
    """Advance a request through ready -> collected -> distributed with ownership checks."""
    allowed_transitions = {
        "ready": ("approved", ("donor",)),
        "collected": ("ready", ("donor", "ngo")),
        "distributed": ("collected", ("ngo",)),
    }
    if target_status not in allowed_transitions:
        raise WorkflowError("Unsupported status transition.")

    required_prev_status, allowed_roles = allowed_transitions[target_status]
    if actor_role not in allowed_roles:
        raise WorkflowError("You are not authorized to perform this status change.")

    now = now_dt()
    ts = _dt_str(now)
    req_meta = fetch_one("SELECT donation_id FROM food_requests WHERE id = %s", (request_id,))
    if not req_meta:
        raise WorkflowError("Food request not found.")
    donation_id = req_meta["donation_id"]

    with transaction() as (_conn, cur):
        # Lock order: donation first, then request
        cur.execute("SELECT * FROM donations WHERE id = %s FOR UPDATE", (donation_id,))
        donation = cur.fetchone()
        cur.execute("SELECT * FROM food_requests WHERE id = %s FOR UPDATE", (request_id,))
        req = cur.fetchone()

        if not donation or not req or req["donation_id"] != donation["id"]:
            raise WorkflowError("Request or donation not found.")

        if actor_role == "donor" and donation["donor_id"] != actor_id:
            raise WorkflowError("You do not own this donation.")
        if actor_role == "ngo" and req["ngo_id"] != actor_id:
            raise WorkflowError("You do not own this request.")

        if req["status"] != required_prev_status:
            raise WorkflowError(
                f"Cannot change status from '{req['status']}' to '{target_status}'."
            )

        if target_status in ("ready", "collected") and str(donation["expires_at"]) <= ts:
            raise WorkflowError("Cannot advance pickup status for an expired donation.")

        cur.execute(
            "UPDATE food_requests SET status = %s, updated_at = %s WHERE id = %s",
            (target_status, ts, request_id),
        )

        qty = _to_decimal(req["requested_quantity"])
        log_history(
            cur,
            donation_id=donation_id,
            request_id=request_id,
            actor_id=actor_id,
            from_status=required_prev_status,
            to_status=target_status,
            quantity=qty,
            note=note or f"Marked {target_status}",
            created_at=now,
        )

        # Mark donation completed when no remaining quantity and no active uncollected requests
        if target_status in ("collected", "distributed") and _to_decimal(donation["available_quantity"]) == Decimal("0.00"):
            cur.execute(
                """
                SELECT COUNT(*) AS cnt FROM food_requests
                WHERE donation_id = %s AND status IN ('pending', 'approved', 'ready')
                """,
                (donation_id,),
            )
            remaining = cur.fetchone()["cnt"]
            if remaining == 0 and donation["status"] != "completed":
                old_don_status = donation["status"]
                cur.execute(
                    "UPDATE donations SET status = 'completed', updated_at = %s WHERE id = %s",
                    (ts, donation_id),
                )
                log_history(
                    cur,
                    donation_id=donation_id,
                    request_id=None,
                    actor_id=actor_id,
                    from_status=old_don_status,
                    to_status="completed",
                    quantity=donation["total_quantity"],
                    note="All quantities collected",
                    created_at=now,
                )

        # Notify counterparty
        if actor_role == "donor":
            recipient_id = req["ngo_id"]
            link = "/ngo/requests"
        else:
            recipient_id = donation["donor_id"]
            link = f"/donor/donations/{donation_id}"

        status_titles = {
            "ready": "Food is ready for pickup",
            "collected": "Food marked as collected",
            "distributed": "Food marked as distributed",
        }
        notify(
            cur,
            user_id=recipient_id,
            title=status_titles[target_status],
            message=f"'{donation['title']}' ({qty} {donation['unit']}) is now {target_status}.",
            link=link,
            donation_id=donation_id,
            request_id=request_id,
            created_at=now,
        )


def expire_overdue_items(reference_time: datetime | None = None) -> int:
    """Expire overdue active donations and their uncollected requests."""
    now = reference_time or now_dt()
    ts = _dt_str(now)
    overdue = fetch_all(
        """
        SELECT id FROM donations
        WHERE status IN ('available', 'partially_reserved', 'reserved')
          AND expires_at <= %s
        """,
        (ts,),
    )
    expired_count = 0
    for row in overdue:
        donation_id = row["id"]
        with transaction() as (_conn, cur):
            cur.execute("SELECT * FROM donations WHERE id = %s FOR UPDATE", (donation_id,))
            donation = cur.fetchone()
            if not donation or donation["status"] not in ("available", "partially_reserved", "reserved"):
                continue
            if str(donation["expires_at"]) > ts:
                continue

            cur.execute(
                """
                SELECT * FROM food_requests
                WHERE donation_id = %s AND status IN ('pending', 'approved', 'ready')
                FOR UPDATE
                """,
                (donation_id,),
            )
            open_reqs = cur.fetchall()
            for req in open_reqs:
                old_req_status = req["status"]
                qty = _to_decimal(req["requested_quantity"])
                cur.execute(
                    "UPDATE food_requests SET status = 'expired', updated_at = %s WHERE id = %s",
                    (ts, req["id"]),
                )
                log_history(
                    cur,
                    donation_id=donation_id,
                    request_id=req["id"],
                    actor_id=None,
                    from_status=old_req_status,
                    to_status="expired",
                    quantity=qty,
                    note="Expired past pickup deadline",
                    created_at=now,
                )
                notify(
                    cur,
                    user_id=req["ngo_id"],
                    title="Food request expired",
                    message=f"Donation '{donation['title']}' expired before collection.",
                    link="/ngo/requests",
                    donation_id=donation_id,
                    request_id=req["id"],
                    created_at=now,
                )

            old_don_status = donation["status"]
            cur.execute(
                "UPDATE donations SET status = 'expired', updated_at = %s WHERE id = %s",
                (ts, donation_id),
            )
            log_history(
                cur,
                donation_id=donation_id,
                request_id=None,
                actor_id=None,
                from_status=old_don_status,
                to_status="expired",
                quantity=_to_decimal(donation["available_quantity"]),
                note="Donation expired past pickup deadline",
                created_at=now,
            )
            notify(
                cur,
                user_id=donation["donor_id"],
                title="Donation expired",
                message=f"Your donation '{donation['title']}' has expired.",
                link=f"/donor/donations/{donation_id}",
                donation_id=donation_id,
                created_at=now,
            )
            expired_count += 1

    return expired_count


def set_ngo_approval(ngo_id: int, admin_id: int, new_status: str, reason: str = "") -> None:
    """Admin approves or rejects an NGO account and notifies the NGO."""
    if new_status not in ("approved", "rejected"):
        raise WorkflowError("Invalid NGO status.")
    now = now_dt()
    ts = _dt_str(now)
    with transaction() as (_conn, cur):
        cur.execute("SELECT * FROM users WHERE id = %s AND role = 'ngo'", (ngo_id,))
        ngo = cur.fetchone()
        if not ngo:
            raise WorkflowError("NGO account not found.")

        cur.execute(
            "UPDATE users SET ngo_status = %s, rejection_reason = %s, updated_at = %s WHERE id = %s",
            (new_status, reason[:255] if new_status == "rejected" else "", ts, ngo_id),
        )
        if new_status == "approved":
            title = "NGO account approved"
            msg = "Your NGO account has been approved by an administrator. You can now request food donations."
        else:
            title = "NGO account rejected"
            msg = f"Your NGO verification was not approved. {reason}".strip()

        notify(
            cur,
            user_id=ngo_id,
            title=title,
            message=msg,
            link="/ngo/dashboard",
            created_at=now,
        )
