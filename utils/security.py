"""Security helpers: password hashing, CSRF protection, and role decorators."""

from __future__ import annotations

import hmac
import secrets
from functools import wraps
from typing import Any, Callable

from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from utils.db import fetch_one


def hash_password(password: str) -> str:
    """Hash a plaintext password using Werkzeug's salted PBKDF2/scrypt hash."""
    return generate_password_hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a plaintext password against its stored hash."""
    if not password_hash or not password:
        return False
    return check_password_hash(password_hash, password)


def generate_csrf_token() -> str:
    """Return the session CSRF token, creating one if absent."""
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf() -> None:
    """Abort with 403 if an unsafe request is missing a valid CSRF token."""
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return
    expected = session.get("_csrf_token")
    provided = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or ""
    )
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        abort(403, description="Invalid or missing CSRF token.")


def load_current_user() -> dict[str, Any] | None:
    """Load the authenticated, active user row for the current session."""
    user_id = session.get("user_id")
    if not user_id:
        g.current_user = None
        return None
    user = fetch_one(
        """
        SELECT u.*, d.name AS district_name, a.name AS area_name
        FROM users u
        LEFT JOIN districts d ON d.id = u.district_id
        LEFT JOIN areas a ON a.id = u.area_id
        WHERE u.id = %s
        """,
        (user_id,),
    )
    if not user or not user.get("is_active"):
        session.clear()
        g.current_user = None
        return None
    g.current_user = user
    return user


def login_required(view: Callable) -> Callable:
    """Require an authenticated active user."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(g, "current_user", None):
            flash("Please sign in to continue.", "error")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles: str) -> Callable:
    """Require an authenticated user holding one of the allowed roles."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user:
                flash("Please sign in to continue.", "error")
                return redirect(url_for("auth.login", next=request.path))
            if user.get("role") not in roles:
                abort(403, description="You do not have permission to access this resource.")
            return view(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(view: Callable) -> Callable:
    """Require an authenticated admin user."""
    return role_required("admin")(view)
