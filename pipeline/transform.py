"""Step 2: clean one year's CSV parts with pandas and keep only rows newer than the watermark.

Why a content hash as the key: the Hub says invoice_id is the unique id, but the 2026
file has 2,200 cases of the same invoice_id + item_no with different quantities (two
line items on one invoice) and 14 exact duplicate rows. So each row gets
line_key = md5 of its raw text: unique per distinct line, and exact repeats collapse.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from pipeline.config import (
    EXPECTED_COLUMNS, INTEGER_COLUMNS, NUMERIC_COLUMNS, OVERLAP_DAYS, PROCESSED_DIR,
)

log = logging.getLogger(__name__)
STRING_COLUMNS = [c for c in EXPECTED_COLUMNS if c not in NUMERIC_COLUMNS and c != "ordered_on"]


def line_keys(raw: pd.DataFrame) -> pd.Series:
    """md5 of the raw row text, columns joined with '|'. Deterministic, so re-runs agree."""
    joined = raw[EXPECTED_COLUMNS[0]].astype(str)
    for c in EXPECTED_COLUMNS[1:]:
        joined = joined + "|" + raw[c].astype(str)
    return joined.map(lambda s: hashlib.md5(s.encode()).hexdigest())


def clean_part(csv_path: Path) -> pd.DataFrame:
    """Read one CSV part as text, then type and tidy every column."""
    raw = pd.read_csv(csv_path, dtype="string[pyarrow]", keep_default_na=False)
    raw = raw.fillna("")
    df = pd.DataFrame({"line_key": line_keys(raw)})
    for c in STRING_COLUMNS:
        df[c] = raw[c].str.strip().replace("", pd.NA)
    df["ordered_on"] = pd.to_datetime(raw["ordered_on"], format="%Y-%m-%d", errors="coerce").dt.date
    for c in NUMERIC_COLUMNS:
        df[c] = pd.to_numeric(raw[c], errors="coerce").astype("float64")
    for c in INTEGER_COLUMNS:
        df[c] = df[c].round().astype("Int64")
    log.info("Cleaned %s: %d rows", csv_path.name, len(df))
    return df


def transform_year(
    csv_paths: list[Path], year: int, run_id: str, since: date | None = None
) -> tuple[Path, dict]:
    """Clean all parts, drop exact duplicates, keep rows after the watermark, write parquet."""
    df = pd.concat([clean_part(p) for p in csv_paths], ignore_index=True)
    rows_in = len(df)
    df = df.drop_duplicates("line_key")
    dupes = rows_in - len(df)
    bad_dates = int(df["ordered_on"].isna().sum())
    df = df[df["ordered_on"].notna()]
    cutoff = None
    if since is not None:
        cutoff = since - timedelta(days=OVERLAP_DAYS)
        df = df[df["ordered_on"] > cutoff]
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    safe_run = re.sub(r"[^A-Za-z0-9_-]", "_", run_id)
    out = PROCESSED_DIR / f"liquor_sales_{year}_{safe_run}.parquet"
    df.to_parquet(out, index=False)
    stats = {
        "rows_in_file": rows_in,
        "exact_duplicates_dropped": dupes,
        "bad_date_rows_dropped": bad_dates,
        "watermark": str(since),
        "cutoff": str(cutoff),
        "rows_to_load": len(df),
        "min_ordered_on": str(df["ordered_on"].min()) if len(df) else None,
        "max_ordered_on": str(df["ordered_on"].max()) if len(df) else None,
        "distinct_stores": int(df["store_no"].nunique()),
    }
    log.info("Transform stats: %s", stats)
    return out, stats


if __name__ == "__main__":
    import argparse
    import glob

    from pipeline.config import DATASET_IDS, RAW_DIR

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Clean an already-downloaded year (no Snowflake needed).")
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--since", type=date.fromisoformat, default=None, help="watermark, e.g. 2026-08-31")
    args = ap.parse_args()
    parts = sorted(Path(p) for p in glob.glob(str(RAW_DIR / str(DATASET_IDS[args.year]) / "*.csv")))
    out, stats = transform_year(parts, args.year, "local", args.since)
    print(out)
    for k, v in stats.items():
        print(f"  {k}: {v}")
