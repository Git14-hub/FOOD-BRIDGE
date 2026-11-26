"""Interactive CLI tool to create an administrator account for FoodBridge."""

from __future__ import annotations

import getpass
import sys

from utils.db import execute, fetch_one
from utils.security import hash_password
from utils.validators import EMAIL_RE, PHONE_RE
from utils.workflow import now_dt


def create_admin_interactive() -> int:
    """Prompt for admin details and insert an admin user."""
    print("Create FoodBridge Administrator")
    print("-------------------------------")
    name = input("Full Name: ").strip()
    if len(name) < 2:
        print("Error: Name must be at least 2 characters.", file=sys.stderr)
        return 1

    email = input("Email Address: ").strip().lower()
    if not EMAIL_RE.match(email):
        print("Error: Invalid email address.", file=sys.stderr)
        return 1

    if fetch_one("SELECT id FROM users WHERE email = %s", (email,)):
        print("Error: A user with that email already exists.", file=sys.stderr)
        return 1

    phone = input("Phone Number: ").strip()
    if not PHONE_RE.match(phone):
        print("Error: Invalid phone number.", file=sys.stderr)
        return 1

    address = input("Office / Contact Address: ").strip() or "Admin Office"
    password = getpass.getpass("Password (min 8 chars): ")
    confirm = getpass.getpass("Confirm Password: ")
    if len(password) < 8 or password != confirm:
        print("Error: Passwords must match and be at least 8 characters.", file=sys.stderr)
        return 1

    ts = now_dt().strftime("%Y-%m-%d %H:%M:%S")
    admin_id = execute(
        """
        INSERT INTO users (
            name, email, password_hash, role, phone,
            organization_name, registration_number, address,
            district_id, area_id, ngo_status, rejection_reason,
            is_active, created_at, updated_at
        ) VALUES (%s, %s, %s, 'admin', %s, 'FoodBridge Admin', '', %s, NULL, NULL, 'not_applicable', '', 1, %s, %s)
        """,
        (name, email, hash_password(password), phone, address, ts, ts),
    )
    print(f"Administrator created successfully (id={admin_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(create_admin_interactive())
