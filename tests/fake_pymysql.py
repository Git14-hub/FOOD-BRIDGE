"""In-memory / file-backed SQLite stand-in for PyMySQL used by tests.

Does NOT simulate MySQL InnoDB row-level `FOR UPDATE` locking across processes;
strips `FOR UPDATE` and translates portable SQL subset for logic verification.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any


class _DictCursorType:
    """Sentinel matching pymysql.cursors.DictCursor."""


class _CursorsNamespace:
    DictCursor = _DictCursorType


cursors = _CursorsNamespace()

_SHARED_SQLITE_PATH: str = ":memory:"
_SHARED_CONN: sqlite3.Connection | None = None


def reset_shared_db(db_path: str = ":memory:") -> None:
    """Reset the shared SQLite connection used across fake_pymysql.connect calls."""
    global _SHARED_SQLITE_PATH, _SHARED_CONN
    if _SHARED_CONN is not None:
        _SHARED_CONN.close()
        _SHARED_CONN = None
    _SHARED_SQLITE_PATH = db_path


def _translate_sql(sql: str) -> str | None:
    """Translate MySQL-compatible DDL/DML in this repo into SQLite SQL."""
    stripped = sql.strip()
    upper = stripped.upper()
    if upper.startswith("CREATE DATABASE") or upper.startswith("USE "):
        return None

    out = stripped
    # Remove table-level MySQL constraints/keywords unsupported by SQLite
    out = re.sub(r"\bINT\s+AUTO_INCREMENT\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", out, flags=re.IGNORECASE)
    out = re.sub(r"\bAUTO_INCREMENT\b", "AUTOINCREMENT", out, flags=re.IGNORECASE)
    out = re.sub(r"\bINSERT\s+IGNORE\b", "INSERT OR IGNORE", out, flags=re.IGNORECASE)
    out = re.sub(r"\bFOR\s+UPDATE\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\bTINYINT\(\d+\)", "INTEGER", out, flags=re.IGNORECASE)
    out = re.sub(r"\bDECIMAL\(\d+\s*,\s*\d+\)", "REAL", out, flags=re.IGNORECASE)
    out = re.sub(r"\bUNIQUE\s+KEY\s+\w+\s*\(", "UNIQUE (", out, flags=re.IGNORECASE)
    # Replace %s parameter placeholders with SQLite ?
    out = out.replace("%s", "?")
    return out


def _dict_factory(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class FakeCursor:
    def __init__(self, inner: sqlite3.Cursor) -> None:
        self._inner = inner

    def execute(self, sql: str, params: tuple | list = ()) -> int:
        translated = _translate_sql(sql)
        if translated is None:
            return 0
        self._inner.execute(translated, tuple(params))
        return self._inner.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._inner.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return self._inner.fetchall()

    @property
    def lastrowid(self) -> int:
        return self._inner.lastrowid

    @property
    def rowcount(self) -> int:
        return self._inner.rowcount

    def close(self) -> None:
        self._inner.close()

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class FakeConnection:
    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._raw.cursor())

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        # Keep the shared in-memory connection alive for the test lifecycle.
        pass


def connect(**_kwargs) -> FakeConnection:
    global _SHARED_CONN
    if _SHARED_CONN is None:
        _SHARED_CONN = sqlite3.connect(_SHARED_SQLITE_PATH, check_same_thread=False)
        _SHARED_CONN.row_factory = _dict_factory
        _SHARED_CONN.execute("PRAGMA foreign_keys = ON")
    return FakeConnection(_SHARED_CONN)
