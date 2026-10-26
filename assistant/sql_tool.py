"""The one tool the assistant gets: run a read-only SQL query against the MARTS schema.

Guardrails, in layers (each one would be enough on its own; together they are defense in depth):
  1. Database role. The connection logs in as ASSISTANT_SVC, which holds only RETAIL_READER:
     SELECT on MARTS tables and nothing else. Snowflake enforces this, not Python.
  2. Statement check. Only a single SELECT or WITH statement is accepted; semicolons,
     comments, and write/DDL keywords are rejected before anything reaches Snowflake.
  3. Row cap. Every query is wrapped in SELECT * FROM (...) LIMIT n, so a careless query
     cannot pull a million rows into the model's context.
  4. Time cap. STATEMENT_TIMEOUT_IN_SECONDS is set on the session.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from pipeline.config import REPO_ROOT, snowflake_settings

MAX_ROWS = 200
TIMEOUT_SECONDS = 30
SCHEMA = "MARTS"

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|create|alter|drop|truncate|grant|revoke|copy|put|get|remove|"
    r"call|execute|use|set|unset|begin|commit|rollback|undrop|describe|show|list)\b",
    re.I,
)


class QueryRejected(Exception):
    """The SQL did not pass the read-only checks. The message says why."""


def reader_settings() -> dict:
    """Read-only connection settings from .env (separate user, separate key)."""
    base = snowflake_settings()
    user = os.getenv("SNOWFLAKE_READER_USER")
    role = os.getenv("SNOWFLAKE_READER_ROLE")
    key = os.getenv("SNOWFLAKE_READER_KEY_PATH")
    if not (user and role and key):
        raise RuntimeError("Missing SNOWFLAKE_READER_USER / SNOWFLAKE_READER_ROLE / SNOWFLAKE_READER_KEY_PATH in .env")
    key_path = Path(key)
    if not key_path.is_absolute():
        key_path = REPO_ROOT / key_path
    return {**base, "user": user, "role": role, "private_key_path": key_path, "schema": SCHEMA}


def connect_reader() -> snowflake.connector.SnowflakeConnection:
    s = reader_settings()
    with open(s["private_key_path"], "rb") as f:
        pkey = serialization.load_pem_private_key(f.read(), password=None)
    der = pkey.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    conn = snowflake.connector.connect(
        account=s["account"], user=s["user"], private_key=der, role=s["role"],
        warehouse=s["warehouse"], database=s["database"], schema=s["schema"],
        session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": TIMEOUT_SECONDS, "QUERY_TAG": "retail-tiering-assistant"},
    )
    return conn


def check_read_only(sql: str) -> str:
    """Return the cleaned SQL or raise QueryRejected. Pure function, easy to test.

    The checks run on the SQL with string literals blanked out, so a value such as
    next_change = 'drop' passes while DROP TABLE does not. (The first eval run caught the
    naive version of this: the model had to work around a false rejection.)
    """
    s = sql.strip().rstrip(";").strip()
    if not s:
        raise QueryRejected("Empty query")
    if s.count("'") % 2:
        raise QueryRejected("Unbalanced quotes")
    structure = re.sub(r"'[^']*'", "''", s)      # literals -> '' ; keywords and ; inside them no longer count
    if ";" in structure:
        raise QueryRejected("Only one statement is allowed (found ';')")
    if "--" in structure or "/*" in structure:
        raise QueryRejected("Comments are not allowed in queries")
    if not re.match(r"^(select|with)\b", structure, re.I):
        raise QueryRejected("Only SELECT (or WITH ... SELECT) queries are allowed")
    bad = _FORBIDDEN.search(structure)
    if bad:
        raise QueryRejected(f"Keyword not allowed in a read-only query: {bad.group(0).upper()}")
    return s


def run_sql(conn, sql: str, max_rows: int = MAX_ROWS) -> dict:
    """Run a read-only query and return columns + rows (capped) + a note if capped."""
    clean = check_read_only(sql)
    wrapped = f"SELECT * FROM ({clean}) LIMIT {max_rows + 1}"
    with conn.cursor() as cur:
        cur.execute(wrapped)
        cols = [d[0].lower() for d in cur.description]
        rows = cur.fetchall()
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    out_rows = [[_plain(v) for v in r] for r in rows]
    return {"columns": cols, "rows": out_rows, "row_count": len(out_rows),
            "truncated": truncated, "note": f"showing first {max_rows} rows only" if truncated else ""}


def _plain(v):
    """Make values JSON-friendly for the model: dates to strings, decimals to numbers."""
    import datetime as dt
    import decimal
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v) if v % 1 else int(v)
    return v


def schema_summary(conn) -> str:
    """Columns of every table in MARTS, as text for the system prompt."""
    db = reader_settings()["database"]
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM {db}.INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{SCHEMA}' ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        rows = cur.fetchall()
    tables: dict[str, list[str]] = {}
    for t, c, typ in rows:
        tables.setdefault(t, []).append(f"{c.lower()} {typ.lower()}")
    return "\n".join(f"{db}.{SCHEMA}.{t}: " + ", ".join(cols) for t, cols in tables.items())


if __name__ == "__main__":
    # Offline self-test of the statement check (no Snowflake needed).
    ok = ["SELECT 1", "with q as (select 1) select * from q", "SELECT * FROM store_tiers WHERE tier = 'A';",
          "SELECT store_id FROM store_tiers WHERE next_change = 'drop'", "select * from t where name = 'DELETE ME; DROP IT'"]
    bad = ["DELETE FROM store_tiers", "SELECT 1; DROP TABLE x", "select 1 -- comment", "UPDATE t SET a=1",
           "CREATE TABLE t AS SELECT 1", "SHOW TABLES", "use role retail_loader", "select * from t where a = 1 /* x */",
           "select * from t where a = 'x", "select 1 union select 1 from t; delete from t"]
    for q in ok:
        assert check_read_only(q), q
    for q in bad:
        try:
            check_read_only(q); raise SystemExit(f"should have been rejected: {q}")
        except QueryRejected as e:
            print(f"rejected OK: {q!r:<48} -> {e}")
    print("statement check: all good")
