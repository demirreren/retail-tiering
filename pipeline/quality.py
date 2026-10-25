"""Data-quality checks that fail loudly. Each raises DataQualityError with a plain message.

- freshness: the newest sale in Snowflake must not be older than N days.
- volume: this run's file row count must be within a ratio band of the previous run's.
- schema drift lives in extract.check_schema, because it must run before any load.
"""
from __future__ import annotations

import logging
from datetime import date

from pipeline.config import FRESHNESS_MAX_AGE_DAYS, RAW_TABLE, VOLUME_MAX_RATIO, VOLUME_MIN_RATIO
from pipeline.load import previous_file_rows

log = logging.getLogger(__name__)


class DataQualityError(Exception):
    """A check failed. The message says which one and by how much."""


def check_freshness(conn, max_age_days: int = FRESHNESS_MAX_AGE_DAYS) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"SELECT MAX(ORDERED_ON) FROM {RAW_TABLE}")
        newest = cur.fetchone()[0]
    if newest is None:
        raise DataQualityError("Freshness: the raw table is empty")
    age = (date.today() - newest).days
    result = {"newest_sale": str(newest), "age_days": age, "max_age_days": max_age_days}
    if age > max_age_days:
        raise DataQualityError(f"Freshness: newest sale is {newest} ({age} days old), limit is {max_age_days} days")
    log.info("Freshness OK: %s", result)
    return result


def check_volume(conn, year: int, rows_in_file: int, run_id: str,
                 min_ratio: float = VOLUME_MIN_RATIO, max_ratio: float = VOLUME_MAX_RATIO) -> dict:
    previous = previous_file_rows(conn, year, exclude_run_id=run_id)
    result = {"rows_in_file": rows_in_file, "previous_rows_in_file": previous}
    if previous is None:
        log.info("Volume: no previous run to compare against, skipping. %s", result)
        return result
    ratio = rows_in_file / previous if previous else float("inf")
    result["ratio"] = round(ratio, 3)
    if not (min_ratio <= ratio <= max_ratio):
        raise DataQualityError(
            f"Volume: file has {rows_in_file:,} rows vs {previous:,} last run "
            f"(ratio {ratio:.2f}, allowed {min_ratio} to {max_ratio})"
        )
    log.info("Volume OK: %s", result)
    return result
