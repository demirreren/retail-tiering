"""Step 3: load the cleaned parquet into Snowflake with an idempotent MERGE.

Idempotent = running it twice gives the same result. Rows whose line_key already
exists are skipped, so the overlap window and task retries never create duplicates.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from snowflake.connector.pandas_tools import write_pandas

from pipeline.config import LOAD_LOG_TABLE, RAW_TABLE, snowflake_settings

log = logging.getLogger(__name__)

RAW_DDL = f"""
CREATE TABLE IF NOT EXISTS {RAW_TABLE} (
    LINE_KEY            VARCHAR(32) NOT NULL,
    INVOICE_ID          VARCHAR,
    ORDERED_ON          DATE,
    STORE_NO            VARCHAR,
    STORE_NAME          VARCHAR,
    STORE_ADDRESS       VARCHAR,
    STORE_CITY          VARCHAR,
    STORE_ZIP_CODE      VARCHAR,
    COUNTY_FIPS_CODE    VARCHAR,
    COUNTY_NAME         VARCHAR,
    CATEGORY_CODE       VARCHAR,
    CATEGORY_NAME       VARCHAR,
    VENDOR_NUMBER       VARCHAR,
    VENDOR_NAME         VARCHAR,
    ITEM_NO             VARCHAR,
    IM_DESC             VARCHAR,
    PACK                NUMBER,
    BOTTLE_VOLUME_ML    NUMBER,
    STATE_BOTTLE_COST   NUMBER(12,2),
    STATE_BOTTLE_RETAIL NUMBER(12,2),
    SALES_BOTTLES       NUMBER,
    SALES_DOLLARS       NUMBER(14,2),
    SALES_LITERS        FLOAT,
    SALES_GALLONS       FLOAT,
    LOADED_AT           TIMESTAMP_NTZ,
    RUN_ID              VARCHAR
)
"""
LOG_DDL = f"""
CREATE TABLE IF NOT EXISTS {LOAD_LOG_TABLE} (
    RUN_ID          VARCHAR,
    DATASET_YEAR    NUMBER,
    LOADED_AT       TIMESTAMP_NTZ,
    ROWS_IN_FILE    NUMBER,
    ROWS_INSERTED   NUMBER,
    MAX_ORDERED_ON  DATE,
    STATUS          VARCHAR
)
"""


def connect() -> snowflake.connector.SnowflakeConnection:
    """Key-pair login as the service user. The private key never leaves this machine."""
    s = snowflake_settings()
    with open(s["private_key_path"], "rb") as f:
        pkey = serialization.load_pem_private_key(f.read(), password=None)
    der = pkey.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return snowflake.connector.connect(
        account=s["account"], user=s["user"], private_key=der, role=s["role"],
        warehouse=s["warehouse"], database=s["database"], schema=s["schema"],
        client_session_keep_alive=False,
    )


def ensure_tables(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(RAW_DDL)
        cur.execute(LOG_DDL)


def get_watermark(conn, year: int) -> date | None:
    """Newest sale date already loaded for this year, or None on the first run."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(ORDERED_ON) FROM {RAW_TABLE} WHERE YEAR(ORDERED_ON) = %s", (year,))
        return cur.fetchone()[0]


def merge_parquet(conn, parquet_path: Path, run_id: str) -> int:
    """Stage the parquet in a temp table, then MERGE new line_keys into the raw table."""
    df = pd.read_parquet(parquet_path)
    if df.empty:
        log.info("Nothing to load")
        return 0
    df["loaded_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    df["run_id"] = run_id
    df.columns = [c.upper() for c in df.columns]
    stage = f"{RAW_TABLE}_STAGE"
    with conn.cursor() as cur:
        cur.execute(f"CREATE OR REPLACE TEMPORARY TABLE {stage} LIKE {RAW_TABLE}")
    ok, n_chunks, n_rows, _ = write_pandas(conn, df, stage, quote_identifiers=False)
    log.info("Staged %d rows in %d chunk(s)", n_rows, n_chunks)
    cols = ", ".join(df.columns)
    src = ", ".join(f"s.{c}" for c in df.columns)
    with conn.cursor() as cur:
        cur.execute(
            f"MERGE INTO {RAW_TABLE} t USING {stage} s ON t.LINE_KEY = s.LINE_KEY "
            f"WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({src})"
        )
        inserted = int(cur.fetchone()[0])
    log.info("MERGE inserted %d new rows (%d already present)", inserted, n_rows - inserted)
    return inserted


def log_run(conn, run_id: str, year: int, rows_in_file: int, rows_inserted: int,
            max_ordered_on: str | None, status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {LOAD_LOG_TABLE} VALUES (%s, %s, CURRENT_TIMESTAMP(), %s, %s, %s, %s)",
            (run_id, year, rows_in_file, rows_inserted, max_ordered_on, status),
        )


def previous_file_rows(conn, year: int, exclude_run_id: str | None = None) -> int | None:
    """Row count of the most recent successful run's file for this year (for the volume check)."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT ROWS_IN_FILE FROM {LOAD_LOG_TABLE} WHERE DATASET_YEAR = %s AND STATUS = 'success' "
            f"AND RUN_ID <> %s ORDER BY LOADED_AT DESC LIMIT 1",
            (year, exclude_run_id or ""),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()")
            print("Connected:", cur.fetchone())
