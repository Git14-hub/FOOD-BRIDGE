# AGENTS.md – development instructions for FoodBridge

Guidance for AI coding agents (and humans) working on this repository.

## Project summary
Flask + PyMySQL + MySQL web app. Roles: donor, NGO (needs admin approval), admin. Core flow: donor lists food →
approved NGOs in the same district+area are notified → NGO requests (partial quantities) → donor approves/rejects →
ready → collected → distributed. All state is in MySQL.

## Layout and responsibilities
* `app.py` – app factory, CSRF/session/expiry hooks, template context, error handlers.
* `modules/*.py` – blueprints. Keep them thin: parse input, call validators/workflow, render.
* `utils/workflow.py` – **all business rules** for reservation, status transitions, notifications, expiry.
  Never change `available_quantity` or request/donation status anywhere else.
* `utils/validators.py` – server-side validation. `utils/security.py` – hashing, CSRF, decorators.
* `utils/db.py` – `fetch_all/fetch_one/execute/transaction`. `database.sql` – single source of truth for the schema.
* `templates/` – `base.html` → `dashboard_base.html` → pages; reusable parts in `_macros.html`.
* `static/css/style.css` – design tokens and components. `tests/test_flow.py` – end-to-end test.

## Rules that must not be broken
1. **SQL is always parameterised** (`%s` + args). Only whitelisted table names may be interpolated.
2. **Every POST form includes** `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
3. **Authorise on the server:** use `@role_required(...)` / `@admin_required`, and check record ownership
   (donor owns donation, NGO owns request). Hiding a button is not security.
4. **Quantity changes happen only inside `transaction()`** after locking the donation row with `FOR UPDATE`
   (lock order: donation first, then request). Reserve on request; release on reject/cancel.
5. Write a `food_collections` row and a notification for every status change (use `log_history` / `notify`).
6. Notifications for new donations go only to **Approved** NGOs with the **same district_id and area_id**.
7. Never hard-code passwords or secrets; config comes from `.env`. Admins are created with `create_admin.py` only.
8. Uploads: extension whitelist + magic-byte check + random file name (`utils/uploads.py`).
9. Keep SQL portable enough for `tests/fake_pymysql.py` (no MySQL-only functions such as `NOW()`; pass Python datetimes).

## UI rules
Palette: bg `#F8F1D5`, green `#477A45`, soft green `#E7EEDC`, white, text `#26352A`, muted `#6B756C`, border `#E4E7DE`.
Font Poppins. Sizes: page title 26px, card title 19px, body 15px, labels 14px, buttons 15px. Reuse existing classes
(`fb-card`, `stat`, `status-badge`, `food-card`, `fb-table`) and macros instead of adding new styles. No heavy
animation, gradients or decorative graphics. Keep pages responsive and form errors visible.

## Workflow for changes
1. Update `database.sql` first if the schema changes (keep it re-runnable: `IF NOT EXISTS`, `INSERT IGNORE`).
2. Put rules in `utils/workflow.py`, routes in the matching module, then templates.
3. Run `python -m tests.test_flow` (SQLite stand-in; does not test MySQL locking) and add checks for new behaviour.
4. Manually verify on real MySQL for anything involving transactions, locking or MySQL-specific SQL.
5. Update `README.md` (including the *Known limitations* list) when behaviour changes.

## Commands
```
python init_db.py          # create DB + tables (re-runnable)
python create_admin.py     # create an admin interactively
python app.py              # run on http://127.0.0.1:5000
python -m tests.test_flow  # logic smoke test
```
