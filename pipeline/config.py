"""Settings for the pipeline. Secrets come from .env (gitignored), never from code."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
ALERT_LOG = DATA_DIR / "alerts.log"

# Iowa Data Hub (it replaced the old Socrata portal in 2026). One dataset per calendar year.
# The API is bulk-only: no filters, no pagination; each call returns a zip of the whole year.
# Ids verified 2026-10-25 from the download filenames: 2013 -> 1250 ... 2026 -> 1263.
API_BASE = "https://idh-be.iowa.gov/api/v1/datasets"
DATASET_IDS = {year: 1250 + (year - 2013) for year in range(2013, 2027)}

# The 23 columns the file had on 2026-10-25. If the publisher renames or drops one,
# the schema-drift check fails before anything is loaded.
EXPECTED_COLUMNS = [
    "invoice_id", "ordered_on", "store_no", "store_name", "store_address", "store_city",
    "store_zip_code", "county_fips_code", "county_name", "category_code", "category_name",
    "vendor_number", "vendor_name", "item_no", "im_desc", "pack", "bottle_volume_ml",
    "state_bottle_cost", "state_bottle_retail", "sales_bottles", "sales_dollars",
    "sales_liters", "sales_gallons",
]
NUMERIC_COLUMNS = [
    "pack", "bottle_volume_ml", "state_bottle_cost", "state_bottle_retail",
    "sales_bottles", "sales_dollars", "sales_liters", "sales_gallons",
]
INTEGER_COLUMNS = ["pack", "bottle_volume_ml", "sales_bottles"]

RAW_TABLE = "LIQUOR_SALES"
LOAD_LOG_TABLE = "LOAD_LOG"

# Data-quality thresholds.
FRESHNESS_MAX_AGE_DAYS = 45   # the feed is monthly with about a one-week lag
VOLUME_MIN_RATIO = 0.5        # this run's file row count vs the previous run's
VOLUME_MAX_RATIO = 2.0
OVERLAP_DAYS = 7              # re-send this many days before the watermark; MERGE skips rows already loaded


def snowflake_settings() -> dict:
    """Connection settings from .env. Fails early with a clear message if something is missing."""
    required = [
        "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH", "SNOWFLAKE_ROLE",
        "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_DATABASE", "SNOWFLAKE_SCHEMA",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Missing in .env: {', '.join(missing)}")
    key_path = Path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"])
    if not key_path.is_absolute():
        key_path = REPO_ROOT / key_path
    return {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "private_key_path": key_path,
        "role": os.environ["SNOWFLAKE_ROLE"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
        "schema": os.environ["SNOWFLAKE_SCHEMA"],
    }
