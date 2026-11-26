"""End-to-end smoke and business-rule tests for FoodBridge using fake_pymysql."""

from __future__ import annotations

import io
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import tests.fake_pymysql as fake_pymysql
from utils import db
from utils.security import hash_password
from utils.workflow import WorkflowError, expire_overdue_items, now_dt


def _extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "CSRF token not found in HTML form"
    return match.group(1)


class FoodBridgeFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        fake_pymysql.reset_shared_db(":memory:")
        db.set_db_module(fake_pymysql)

        from init_db import init_database
        init_database()

        self.temp_uploads = tempfile.TemporaryDirectory()
        from app import create_app
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["UPLOAD_FOLDER"] = self.temp_uploads.name

        # Create admin directly in DB (simulating create_admin.py)
        ts = now_dt().strftime("%Y-%m-%d %H:%M:%S")
        self.admin_id = db.execute(
            """
            INSERT INTO users (
                name, email, password_hash, role, phone,
                organization_name, registration_number, address,
                district_id, area_id, ngo_status, rejection_reason,
                is_active, created_at, updated_at
            ) VALUES (%s, %s, %s, 'admin', %s, 'Admin', '', 'HQ', NULL, NULL, 'not_applicable', '', 1, %s, %s)
            """,
            ("Admin User", "admin@foodbridge.test", hash_password("adminpass123"), "9999999999", ts, ts),
        )

    def tearDown(self) -> None:
        self.temp_uploads.cleanup()
        fake_pymysql.reset_shared_db(":memory:")

    def _register_user(self, client, role: str, email: str, district_id: int, area_id: int, org_name: str = ""):
        page = client.get("/register")
        self.assertEqual(page.status_code, 200)
        csrf = _extract_csrf(page.get_data(as_text=True))
        payload = {
            "csrf_token": csrf,
            "role": role,
            "name": f"{role.title()} {email.split('@')[0]}",
            "email": email,
            "phone": "555-010-9999",
            "address": "123 Community Street",
            "district_id": str(district_id),
            "area_id": str(area_id),
            "password": "password123",
            "confirm_password": "password123",
        }
        if role == "ngo":
            payload["organization_name"] = org_name or f"NGO {email.split('@')[0]}"
            payload["registration_number"] = "REG-1001"
        resp = client.post("/register", data=payload, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        return db.fetch_one("SELECT * FROM users WHERE email = %s", (email,))

    def _login(self, client, email: str, password: str = "password123") -> None:
        page = client.get("/login")
        csrf = _extract_csrf(page.get_data(as_text=True))
        resp = client.post(
            "/login",
            data={"csrf_token": csrf, "email": email, "password": password},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)

    def test_csrf_enforced_on_post(self) -> None:
        client = self.app.test_client()
        resp = client.post("/login", data={"email": "a@b.com", "password": "secret"})
        self.assertEqual(resp.status_code, 403)

    def test_upload_magic_byte_validation(self) -> None:
        donor_client = self.app.test_client()
        self._register_user(donor_client, "donor", "donor_img@test.com", district_id=1, area_id=1)

        page = donor_client.get("/donor/donations/new")
        csrf = _extract_csrf(page.get_data(as_text=True))
        future = (datetime.now() + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M")

        # Fake PNG with invalid magic bytes must fail
        bad_resp = donor_client.post(
            "/donor/donations/new",
            data={
                "csrf_token": csrf,
                "title": "Fresh Bread Loaves",
                "category": "Bakery & Bread",
                "quantity": "10",
                "unit": "packs",
                "district_id": "1",
                "area_id": "1",
                "pickup_address": "10 Baker Street",
                "expires_at": future,
                "image": (io.BytesIO(b"not-a-real-png"), "fake.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertIn("File content does not match", bad_resp.get_data(as_text=True))

        # Valid PNG magic bytes should succeed
        valid_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        good_resp = donor_client.post(
            "/donor/donations/new",
            data={
                "csrf_token": csrf,
                "title": "Fresh Bread Loaves",
                "category": "Bakery & Bread",
                "quantity": "10",
                "unit": "packs",
                "district_id": "1",
                "area_id": "1",
                "pickup_address": "10 Baker Street",
                "expires_at": future,
                "image": (io.BytesIO(valid_png), "bread.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(good_resp.status_code, 200)
        don = db.fetch_one("SELECT * FROM donations WHERE title = %s", ("Fresh Bread Loaves",))
        self.assertIsNotNone(don)
        self.assertTrue(don["image_path"].startswith("uploads/") and don["image_path"].endswith(".png"))

    def test_end_to_end_foodbridge_flow_and_rules(self) -> None:
        donor_client = self.app.test_client()
        ngo_same_approved_client = self.app.test_client()
        ngo_same_pending_client = self.app.test_client()
        ngo_other_area_client = self.app.test_client()
        admin_client = self.app.test_client()

        # 1. Register donor and 3 NGOs
        donor = self._register_user(donor_client, "donor", "donor@test.com", district_id=1, area_id=1)
        ngo_ok = self._register_user(ngo_same_approved_client, "ngo", "ngo_ok@test.com", district_id=1, area_id=1, org_name="Hope Kitchen")
        ngo_pending = self._register_user(ngo_same_pending_client, "ngo", "ngo_pending@test.com", district_id=1, area_id=1, org_name="Pending Aid")
        ngo_other = self._register_user(ngo_other_area_client, "ngo", "ngo_other@test.com", district_id=1, area_id=2, org_name="Other Area NGO")

        # 2. Admin approves ngo_ok and ngo_other, leaves ngo_pending as pending
        self._login(admin_client, "admin@foodbridge.test", "adminpass123")
        ngos_page = admin_client.get("/admin/ngos")
        csrf = _extract_csrf(ngos_page.get_data(as_text=True))
        admin_client.post(f"/admin/ngos/{ngo_ok['id']}/approve", data={"csrf_token": csrf}, follow_redirects=True)
        admin_client.post(f"/admin/ngos/{ngo_other['id']}/approve", data={"csrf_token": csrf}, follow_redirects=True)

        self.assertEqual(db.fetch_one("SELECT ngo_status FROM users WHERE id = %s", (ngo_ok["id"],))["ngo_status"], "approved")
        self.assertEqual(db.fetch_one("SELECT ngo_status FROM users WHERE id = %s", (ngo_pending["id"],))["ngo_status"], "pending")

        # 3. Donor lists 20 portions in district 1, area 1
        new_page = donor_client.get("/donor/donations/new")
        donor_csrf = _extract_csrf(new_page.get_data(as_text=True))
        expires = (datetime.now() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M")
        donor_client.post(
            "/donor/donations/new",
            data={
                "csrf_token": donor_csrf,
                "title": "Vegetable Rice Bowls",
                "category": "Cooked Meals",
                "description": "Packed hot in insulated containers.",
                "quantity": "20",
                "unit": "portions",
                "district_id": "1",
                "area_id": "1",
                "pickup_address": "42 Market Lane",
                "expires_at": expires,
            },
            follow_redirects=True,
        )
        donation = db.fetch_one("SELECT * FROM donations WHERE donor_id = %s", (donor["id"],))
        self.assertIsNotNone(donation)
        self.assertEqual(Decimal(str(donation["available_quantity"])), Decimal("20"))

        # Rule 6 check: ONLY approved NGO in same district+area got the new donation notification
        notifs_ok = db.fetch_all(
            "SELECT * FROM notifications WHERE user_id = %s AND donation_id = %s",
            (ngo_ok["id"], donation["id"]),
        )
        notifs_pending = db.fetch_all(
            "SELECT * FROM notifications WHERE user_id = %s AND donation_id = %s",
            (ngo_pending["id"], donation["id"]),
        )
        notifs_other = db.fetch_all(
            "SELECT * FROM notifications WHERE user_id = %s AND donation_id = %s",
            (ngo_other["id"], donation["id"]),
        )
        self.assertEqual(len(notifs_ok), 1)
        self.assertEqual(len(notifs_pending), 0)
        self.assertEqual(len(notifs_other), 0)

        # 4. Pending NGO cannot request food
        det_pending = ngo_same_pending_client.get(f"/ngo/donations/{donation['id']}")
        csrf_pending = _extract_csrf(det_pending.get_data(as_text=True))
        resp_pending_req = ngo_same_pending_client.post(
            f"/ngo/donations/{donation['id']}/request",
            data={"csrf_token": csrf_pending, "requested_quantity": "5"},
            follow_redirects=True,
        )
        self.assertIn("must be approved", resp_pending_req.get_data(as_text=True))
        self.assertEqual(len(db.fetch_all("SELECT * FROM food_requests WHERE donation_id = %s", (donation["id"],))), 0)

        # 5. Approved NGO requests 8 portions -> reserved immediately (available_quantity = 12)
        det_ok = ngo_same_approved_client.get(f"/ngo/donations/{donation['id']}")
        csrf_ok = _extract_csrf(det_ok.get_data(as_text=True))
        ngo_same_approved_client.post(
            f"/ngo/donations/{donation['id']}/request",
            data={"csrf_token": csrf_ok, "requested_quantity": "8", "ngo_note": "Van arriving 4 PM"},
            follow_redirects=True,
        )
        donation = db.fetch_one("SELECT * FROM donations WHERE id = %s", (donation["id"],))
        self.assertEqual(Decimal(str(donation["available_quantity"])), Decimal("12"))
        self.assertEqual(donation["status"], "partially_reserved")

        req1 = db.fetch_one("SELECT * FROM food_requests WHERE donation_id = %s AND ngo_id = %s", (donation["id"], ngo_ok["id"]))
        self.assertEqual(req1["status"], "pending")

        # 6. Other approved NGO requests 5 portions, then donor rejects -> 5 released back (avail = 12)
        det_other = ngo_other_area_client.get(f"/ngo/donations/{donation['id']}")
        csrf_other = _extract_csrf(det_other.get_data(as_text=True))
        ngo_other_area_client.post(
            f"/ngo/donations/{donation['id']}/request",
            data={"csrf_token": csrf_other, "requested_quantity": "5"},
            follow_redirects=True,
        )
        donation = db.fetch_one("SELECT * FROM donations WHERE id = %s", (donation["id"],))
        self.assertEqual(Decimal(str(donation["available_quantity"])), Decimal("7"))

        req2 = db.fetch_one("SELECT * FROM food_requests WHERE donation_id = %s AND ngo_id = %s", (donation["id"], ngo_other["id"]))
        # Authorization check: ngo_other cannot cancel req1 belonging to ngo_ok
        forbidden = ngo_other_area_client.post(
            f"/ngo/requests/{req1['id']}/cancel",
            data={"csrf_token": csrf_other},
        )
        self.assertEqual(forbidden.status_code, 403)

        # Donor rejects req2 -> available_quantity goes back from 7 to 12
        donor_det = donor_client.get(f"/donor/donations/{donation['id']}")
        donor_det_csrf = _extract_csrf(donor_det.get_data(as_text=True))
        donor_client.post(
            f"/donor/requests/{req2['id']}/reject",
            data={"csrf_token": donor_det_csrf},
            follow_redirects=True,
        )
        donation = db.fetch_one("SELECT * FROM donations WHERE id = %s", (donation["id"],))
        self.assertEqual(Decimal(str(donation["available_quantity"])), Decimal("12"))

        # 7. NGO requests remaining 12 portions, then cancels -> released back to 12
        ngo_same_approved_client.post(
            f"/ngo/donations/{donation['id']}/request",
            data={"csrf_token": csrf_ok, "requested_quantity": "12"},
            follow_redirects=True,
        )
        donation = db.fetch_one("SELECT * FROM donations WHERE id = %s", (donation["id"],))
        self.assertEqual(Decimal(str(donation["available_quantity"])), Decimal("0"))
        self.assertEqual(donation["status"], "reserved")

        req3 = db.fetch_one(
            "SELECT * FROM food_requests WHERE donation_id = %s AND requested_quantity = 12",
            (donation["id"],),
        )
        ngo_same_approved_client.post(
            f"/ngo/requests/{req3['id']}/cancel",
            data={"csrf_token": csrf_ok},
            follow_redirects=True,
        )
        donation = db.fetch_one("SELECT * FROM donations WHERE id = %s", (donation["id"],))
        self.assertEqual(Decimal(str(donation["available_quantity"])), Decimal("12"))
        self.assertEqual(donation["status"], "partially_reserved")

        # 8. Complete lifecycle on req1: pending -> approved -> ready -> collected -> distributed
        donor_client.post(f"/donor/requests/{req1['id']}/approve", data={"csrf_token": donor_det_csrf}, follow_redirects=True)
        donor_client.post(f"/donor/requests/{req1['id']}/ready", data={"csrf_token": donor_det_csrf}, follow_redirects=True)
        ngo_same_approved_client.post(f"/ngo/requests/{req1['id']}/collected", data={"csrf_token": csrf_ok}, follow_redirects=True)
        ngo_same_approved_client.post(f"/ngo/requests/{req1['id']}/distributed", data={"csrf_token": csrf_ok}, follow_redirects=True)

        final_req1 = db.fetch_one("SELECT * FROM food_requests WHERE id = %s", (req1["id"],))
        self.assertEqual(final_req1["status"], "distributed")

        history_statuses = [
            row["to_status"]
            for row in db.fetch_all(
                "SELECT to_status FROM food_collections WHERE request_id = %s ORDER BY id ASC",
                (req1["id"],),
            )
        ]
        self.assertEqual(history_statuses, ["pending", "approved", "ready", "collected", "distributed"])

        # 9. Role isolation & ownership: donor cannot access admin routes or another donor's donation
        admin_attempt = donor_client.get("/admin/dashboard")
        self.assertEqual(admin_attempt.status_code, 403)

        donor2_client = self.app.test_client()
        self._register_user(donor2_client, "donor", "donor2@test.com", district_id=1, area_id=1)
        self.assertEqual(donor2_client.get(f"/donor/donations/{donation['id']}").status_code, 403)

        # 10. Donor cancels donation -> remaining open state becomes cancelled
        donor_client.post(
            f"/donor/donations/{donation['id']}/cancel",
            data={"csrf_token": donor_det_csrf, "note": "Kitchen closed early"},
            follow_redirects=True,
        )
        cancelled_don = db.fetch_one("SELECT status FROM donations WHERE id = %s", (donation["id"],))
        self.assertEqual(cancelled_don["status"], "cancelled")

        # 11. Notifications read flow & admin locations + NGO rejection
        notif_page = ngo_same_approved_client.get("/notifications")
        self.assertEqual(notif_page.status_code, 200)
        notif_csrf = _extract_csrf(notif_page.get_data(as_text=True))
        ngo_same_approved_client.post("/notifications/read-all", data={"csrf_token": notif_csrf}, follow_redirects=True)
        unread_after = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id = %s AND is_read = 0",
            (ngo_ok["id"],),
        )
        self.assertEqual(unread_after["cnt"], 0)

        loc_page = admin_client.get("/admin/locations")
        loc_csrf = _extract_csrf(loc_page.get_data(as_text=True))
        admin_client.post("/admin/locations", data={"csrf_token": loc_csrf, "kind": "district", "name": "East District"})
        self.assertIsNotNone(db.fetch_one("SELECT id FROM districts WHERE name = %s", ("East District",)))

        admin_client.post(
            f"/admin/ngos/{ngo_pending['id']}/reject",
            data={"csrf_token": csrf, "reason": "Incomplete paperwork"},
            follow_redirects=True,
        )
        self.assertEqual(
            db.fetch_one("SELECT ngo_status FROM users WHERE id = %s", (ngo_pending["id"],))["ngo_status"],
            "rejected",
        )

    def test_expiry_marks_overdue_donations_and_open_requests_expired(self) -> None:
        donor_client = self.app.test_client()
        ngo_client = self.app.test_client()
        admin_client = self.app.test_client()

        donor = self._register_user(donor_client, "donor", "exp_donor@test.com", 1, 1)
        ngo = self._register_user(ngo_client, "ngo", "exp_ngo@test.com", 1, 1, org_name="Exp NGO")
        self._login(admin_client, "admin@foodbridge.test", "adminpass123")
        csrf_admin = _extract_csrf(admin_client.get("/admin/ngos").get_data(as_text=True))
        admin_client.post(f"/admin/ngos/{ngo['id']}/approve", data={"csrf_token": csrf_admin})

        # Create donation expiring in 1 hour
        csrf_donor = _extract_csrf(donor_client.get("/donor/donations/new").get_data(as_text=True))
        soon = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        donor_client.post(
            "/donor/donations/new",
            data={
                "csrf_token": csrf_donor,
                "title": "Fruit Crate",
                "category": "Fruits & Vegetables",
                "quantity": "15",
                "unit": "kg",
                "district_id": "1",
                "area_id": "1",
                "pickup_address": "1 Orchard Rd",
                "expires_at": soon,
            },
        )
        donation = db.fetch_one("SELECT * FROM donations WHERE donor_id = %s", (donor["id"],))
        csrf_ngo = _extract_csrf(ngo_client.get(f"/ngo/donations/{donation['id']}").get_data(as_text=True))
        ngo_client.post(
            f"/ngo/donations/{donation['id']}/request",
            data={"csrf_token": csrf_ngo, "requested_quantity": "5"},
        )

        # Simulate time advancing past expiry
        expired_count = expire_overdue_items(reference_time=datetime.now() + timedelta(hours=2))
        self.assertEqual(expired_count, 1)

        don_after = db.fetch_one("SELECT status FROM donations WHERE id = %s", (donation["id"],))
        req_after = db.fetch_one("SELECT status FROM food_requests WHERE donation_id = %s", (donation["id"],))
        self.assertEqual(don_after["status"], "expired")
        self.assertEqual(req_after["status"], "expired")


if __name__ == "__main__":
    unittest.main(verbosity=2)
