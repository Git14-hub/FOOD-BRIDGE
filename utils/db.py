"""Database access helpers for FoodBridge using PyMySQL."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
import pymysql.cursors

from config import Config

# Allow test harness to substitute a compatible fake_pymysql module or custom connector.
_pymysql_module = pymysql


def set_db_module(module: Any) -> None:
    """Override the PyMySQL-compatible module (used by tests)."""
    global _pymysql_module
    _pymysql_module = module


def get_connection(use_database: bool = True):
    """Open a PyMySQL connection returning dict rows."""
    kwargs: dict[str, Any] = {
        "host": Config.DB_HOST,
        "port": Config.DB_PORT,
        "user": Config.DB_USER,
        "password": Config.DB_PASSWORD,
        "charset": "utf8mb4",
        "cursorclass": _pymysql_module.cursors.DictCursor,
        "autocommit": False,
    }
    if use_database:
        kwargs["database"] = Config.DB_NAME
    return _pymysql_module.connect(**kwargs)


def fetch_all(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    """Execute a read query and return all rows."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def fetch_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    """Execute a read query and return a single row or None."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def execute(sql: str, params: tuple | list = ()) -> int:
    """Execute a single write statement inside its own committed transaction.

    Returns the cursor's lastrowid (or rowcount if lastrowid is 0).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            last_id = getattr(cur, "lastrowid", 0)
            rowcount = cur.rowcount
        conn.commit()
        return last_id or rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[tuple[Any, Any]]:
    """Context manager yielding (conn, cursor) with atomic commit/rollback."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        try:
            yield conn, cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    finally:
        conn.close()
