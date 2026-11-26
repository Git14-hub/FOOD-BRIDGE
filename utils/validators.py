"""Server-side input validators for FoodBridge."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from utils.db import fetch_one

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^[0-9+\-\s()]{7,25}$")

FOOD_CATEGORIES = (
    "Cooked Meals",
    "Dry Groceries",
    "Bakery & Bread",
    "Fruits & Vegetables",
    "Dairy & Perishables",
    "Packaged Foods",
)

FOOD_UNITS = ("kg", "portions", "packs", "boxes", "litres", "trays")


def parse_int(value: Any) -> int | None:
    """Parse a positive/non-empty integer or return None."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_positive_decimal(value: Any, max_value: Decimal = Decimal("100000")) -> Decimal | None:
    """Parse a strictly positive decimal up to max_value, quantized to 2 decimals."""
    try:
        raw = str(value).strip()
        if not raw:
            return None
        num = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not num.is_finite() or num <= Decimal("0") or num > max_value:
        return None
    return num.quantize(Decimal("0.01"))


def parse_datetime_input(value: Any) -> datetime | None:
    """Parse HTML datetime-local or standard ISO/SQL datetime strings."""
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def validate_location_ids(district_id_raw: Any, area_id_raw: Any) -> tuple[int | None, int | None, str | None]:
    """Verify that district_id and area_id exist and that area belongs to district."""
    district_id = parse_int(district_id_raw)
    area_id = parse_int(area_id_raw)
    if not district_id or not area_id:
        return None, None, "Please select both a valid district and area."

    area = fetch_one(
        "SELECT id, district_id FROM areas WHERE id = %s AND district_id = %s",
        (area_id, district_id),
    )
    if not area:
        return None, None, "Selected area does not belong to the chosen district."
    return district_id, area_id, None


def validate_registration(form: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate public registration form (donor or ngo only)."""
    errors: dict[str, str] = {}
    role = str(form.get("role") or "").strip().lower()
    if role not in ("donor", "ngo"):
        errors["role"] = "Please choose either Donor or NGO."

    name = str(form.get("name") or "").strip()
    if len(name) < 2 or len(name) > 120:
        errors["name"] = "Full name must be between 2 and 120 characters."

    email = str(form.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 190:
        errors["email"] = "Enter a valid email address."

    phone = str(form.get("phone") or "").strip()
    if not PHONE_RE.match(phone):
        errors["phone"] = "Enter a valid phone number (7-25 digits/symbols)."

    address = str(form.get("address") or "").strip()
    if len(address) < 5 or len(address) > 255:
        errors["address"] = "Address must be between 5 and 255 characters."

    organization_name = str(form.get("organization_name") or "").strip()
    registration_number = str(form.get("registration_number") or "").strip()
    if role == "ngo":
        if len(organization_name) < 2 or len(organization_name) > 160:
            errors["organization_name"] = "NGO organization name is required (2-160 characters)."
        if len(registration_number) < 2 or len(registration_number) > 100:
            errors["registration_number"] = "NGO registration number is required."

    district_id, area_id, loc_err = validate_location_ids(form.get("district_id"), form.get("area_id"))
    if loc_err:
        errors["area_id"] = loc_err

    password = str(form.get("password") or "")
    confirm_password = str(form.get("confirm_password") or "")
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters long."
    elif password != confirm_password:
        errors["confirm_password"] = "Passwords do not match."

    cleaned = {
        "role": role,
        "name": name,
        "email": email,
        "phone": phone,
        "address": address,
        "organization_name": organization_name,
        "registration_number": registration_number,
        "district_id": district_id,
        "area_id": area_id,
        "password": password,
    }
    return cleaned, errors


def validate_profile_update(form: dict[str, Any], role: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate profile update fields."""
    errors: dict[str, str] = {}
    name = str(form.get("name") or "").strip()
    if len(name) < 2 or len(name) > 120:
        errors["name"] = "Full name must be between 2 and 120 characters."

    phone = str(form.get("phone") or "").strip()
    if not PHONE_RE.match(phone):
        errors["phone"] = "Enter a valid phone number."

    address = str(form.get("address") or "").strip()
    if len(address) < 5 or len(address) > 255:
        errors["address"] = "Address must be between 5 and 255 characters."

    organization_name = str(form.get("organization_name") or "").strip()
    if role == "ngo" and (len(organization_name) < 2 or len(organization_name) > 160):
        errors["organization_name"] = "Organization name must be between 2 and 160 characters."

    district_id, area_id, loc_err = validate_location_ids(form.get("district_id"), form.get("area_id"))
    if loc_err:
        errors["area_id"] = loc_err

    cleaned = {
        "name": name,
        "phone": phone,
        "address": address,
        "organization_name": organization_name,
        "district_id": district_id,
        "area_id": area_id,
    }
    return cleaned, errors


def validate_donation_form(
    form: dict[str, Any],
    now: datetime,
    is_edit: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate food donation creation or edit form."""
    errors: dict[str, str] = {}

    title = str(form.get("title") or "").strip()
    if len(title) < 3 or len(title) > 160:
        errors["title"] = "Food title must be between 3 and 160 characters."

    category = str(form.get("category") or "").strip()
    if category not in FOOD_CATEGORIES:
        errors["category"] = "Please select a valid food category."

    description = str(form.get("description") or "").strip()
    if len(description) > 1000:
        errors["description"] = "Description must be 1000 characters or fewer."

    quantity = None
    unit = str(form.get("unit") or "").strip()
    if not is_edit:
        quantity = parse_positive_decimal(form.get("quantity"))
        if quantity is None:
            errors["quantity"] = "Enter a valid positive quantity."
        if unit not in FOOD_UNITS:
            errors["unit"] = "Select a valid unit."

    pickup_address = str(form.get("pickup_address") or "").strip()
    if len(pickup_address) < 5 or len(pickup_address) > 255:
        errors["pickup_address"] = "Pickup address must be between 5 and 255 characters."

    district_id, area_id, loc_err = validate_location_ids(form.get("district_id"), form.get("area_id"))
    if loc_err:
        errors["area_id"] = loc_err

    prepared_raw = str(form.get("prepared_at") or "").strip()
    prepared_at = None
    if prepared_raw:
        prepared_at = parse_datetime_input(prepared_raw)
        if prepared_at is None:
            errors["prepared_at"] = "Enter a valid prepared date/time."

    expires_at = parse_datetime_input(form.get("expires_at"))
    if expires_at is None:
        errors["expires_at"] = "Enter a valid pickup deadline / expiry date and time."
    elif expires_at <= now:
        errors["expires_at"] = "Expiry time must be in the future."
    elif prepared_at and expires_at <= prepared_at:
        errors["expires_at"] = "Expiry time must be after prepared time."

    cleaned = {
        "title": title,
        "category": category,
        "description": description,
        "quantity": quantity,
        "unit": unit,
        "pickup_address": pickup_address,
        "district_id": district_id,
        "area_id": area_id,
        "prepared_at": prepared_at,
        "expires_at": expires_at,
    }
    return cleaned, errors


def validate_food_request_form(form: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate NGO partial-quantity food request input."""
    errors: dict[str, str] = {}
    qty = parse_positive_decimal(form.get("requested_quantity"))
    if qty is None:
        errors["requested_quantity"] = "Enter a positive quantity to request."

    ngo_note = str(form.get("ngo_note") or "").strip()
    if len(ngo_note) > 255:
        errors["ngo_note"] = "Note must be 255 characters or fewer."

    return {"requested_quantity": qty, "ngo_note": ngo_note}, errors
