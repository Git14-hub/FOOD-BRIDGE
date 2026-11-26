# FoodBridge

Surplus food redistribution platform built with Flask, PyMySQL, and MySQL.

## Overview
FoodBridge connects surplus food **Donors** with admin-approved **NGOs** in the same district and area:
1. Donors list surplus food with quantity, category, pickup address, expiry deadline, and an optional photo.
2. **Approved** NGOs in the matching `district_id` and `area_id` receive notifications.
3. Approved NGOs request full or partial quantities (reserved atomically inside a MySQL transaction).
4. Donors approve or reject requests; rejected or cancelled requests release reserved quantity back.
5. Pickups progress through `pending` → `approved` → `ready` → `collected` → `distributed` with full `food_collections` audit history and notifications at each step.
6. Administrators approve/reject NGO accounts, oversee donations/users, and manage districts & areas.

## Setup & Commands

1. Copy `.env.example` to `.env` and configure your MySQL credentials and `FLASK_SECRET_KEY`:
   ```bash
   copy .env.example .env
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Initialize the MySQL database and tables (re-runnable):
   ```bash
   python init_db.py
   ```
4. Create an admin account interactively:
   ```bash
   python create_admin.py
   ```
5. Run the development server on `http://127.0.0.1:5000`:
   ```bash
   python app.py
   ```
6. Run the end-to-end logic test suite (uses the SQLite stand-in `tests/fake_pymysql.py`):
   ```bash
   python -m tests.test_flow
   ```

## Architecture
- `app.py`: App factory, CSRF enforcement, user session loading, overdue expiry hook, template context, and error handlers.
- `modules/`: Thin Flask blueprints (`auth.py`, `donor.py`, `ngo.py`, `admin.py`, `notifications.py`).
- `utils/workflow.py`: All reservation, status transition, notification, history logging, and expiry rules.
- `utils/validators.py`: Server-side validation for auth, donations, partial requests, and locations.
- `utils/security.py`: Password hashing, CSRF tokens, and `@login_required` / `@role_required` / `@admin_required`.
- `utils/uploads.py`: Extension whitelist, magic-byte verification, and random UUID image naming.
- `utils/db.py`: Parameterized `fetch_all`, `fetch_one`, `execute`, and `transaction()` helpers.
- `database.sql`: Single source of truth for the MySQL schema and starter districts/areas.

## Known limitations
- `python -m tests.test_flow` runs against `tests/fake_pymysql.py` (SQLite), which strips `FOR UPDATE` and does not test concurrent MySQL InnoDB row-level locking; verify transactional concurrency directly against a real MySQL instance.
- Overdue donations/requests are expired lazily on incoming requests via `expire_overdue_items()` in `app.py` rather than via a background cron worker.
- Notifications are stored in-app in MySQL (no external email/SMS delivery configured).
- Uploaded food photos are stored locally on disk under `static/uploads/`.
