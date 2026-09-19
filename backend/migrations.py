"""Additive, idempotent schema migration.

`Base.metadata.create_all` creates missing TABLES but never alters an existing one, so a database
that predates the account-security columns would fail at runtime. This module fills the gap
without a migration framework: for every mapped table that already exists, any model column
missing from the live table is added with ``ALTER TABLE ... ADD COLUMN``.

Only ADDITIVE changes are made — no column is dropped, retyped or reordered, and existing rows
keep their data (new NOT NULL columns receive a sane DEFAULT).
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from backend.database import Base


def _literal(value: object, col_type: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _zero_for(col_type: object) -> object:
    name = type(col_type).__name__.lower()
    if "bool" in name:
        return False
    if "int" in name:
        return 0
    if "float" in name or "numeric" in name or "decimal" in name:
        return 0.0
    return ""


def _column_ddl(dialect, table_name: str, col) -> str:
    type_sql = col.type.compile(dialect=dialect)
    sql = f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {type_sql}'
    default = None
    if getattr(col, "server_default", None) is not None:
        default = getattr(col.server_default, "arg", None)
        if default is not None and not isinstance(default, (str, int, float, bool)):
            default = str(default)
    elif getattr(col, "default", None) is not None and getattr(col.default, "is_scalar", False):
        default = col.default.arg
        if callable(default):
            default = None
    if default is None and not col.nullable:
        default = _zero_for(col.type)
    if default is not None and not isinstance(default, str):
        sql += " DEFAULT " + _literal(default, col.type)
    elif default is not None:
        # A string default may come from a SQL expression (e.g. "now()"); only quote plain values.
        if default.endswith(")") or default.upper() in {"CURRENT_TIMESTAMP"}:
            sql += f" DEFAULT {default}"
        else:
            sql += " DEFAULT " + _literal(default, col.type)
    if not col.nullable:
        sql += " NOT NULL"
    return sql


def ensure_schema(engine: Engine) -> list[str]:
    """Add any missing columns. Returns the list of applied ``table.column`` changes."""
    inspector = inspect(engine)
    applied: list[str] = []
    dialect = engine.dialect
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = _column_ddl(dialect, table.name, col)
            with engine.begin() as conn:
                conn.execute(text(ddl))
            applied.append(f"{table.name}.{col.name}")
    return applied
