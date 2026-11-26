"""Initialize the FoodBridge MySQL database and tables from database.sql."""

from __future__ import annotations

from pathlib import Path

from utils.db import get_connection


def _split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(current).strip()
            if stmt:
                statements.append(stmt[:-1])  # drop trailing semicolon
            current = []
    leftover = "\n".join(current).strip()
    if leftover:
        statements.append(leftover)
    return statements


def init_database(schema_path: Path | None = None) -> None:
    """Execute database.sql statements idempotently."""
    target = schema_path or (Path(__file__).resolve().parent / "database.sql")
    sql_text = target.read_text(encoding="utf-8")
    statements = _split_sql_statements(sql_text)

    conn = get_connection(use_database=False)
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
    print("FoodBridge database initialized successfully.")
