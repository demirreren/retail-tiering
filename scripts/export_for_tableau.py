"""Export the three mart tables to CSV for the Tableau Public dashboard.

Tableau Public cannot connect to Snowflake (only the paid Desktop edition can), so the
dashboard is built from these files. Run after tiers.py and predict_drop.py have written
their tables:

    .venv/bin/python scripts/export_for_tableau.py

Writes data/exports/store_quarter.csv, store_tiers.csv and tier_drop_risk.csv (gitignored).
The connection is the loader's key-pair login from pipeline.load.connect; the script only
runs SELECTs. One column is added on the way out: state = "Iowa", because Tableau geocodes a
county name only when a state column sits next to it.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))   # so `python scripts/export_for_tableau.py` can import pipeline/

from pipeline.config import DATA_DIR, snowflake_settings  # noqa: E402
from pipeline.load import connect  # noqa: E402

log = logging.getLogger(__name__)
EXPORT_DIR = DATA_DIR / "exports"
TABLES = ["STORE_QUARTER", "STORE_TIERS", "TIER_DROP_RISK"]


def export_table(conn, table: str, out_dir: Path = EXPORT_DIR) -> Path:
    """SELECT * from one MARTS table and write it as <table>.csv with lower-case headers."""
    db = snowflake_settings()["database"]
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {db}.MARTS.{table}")
        df = cur.fetch_pandas_all()
    df.columns = [c.lower() for c in df.columns]
    df["state"] = "Iowa"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{table.lower()}.csv"
    df.to_csv(out, index=False)
    log.info("%s: %s rows, %d columns -> %s", table, f"{len(df):,}", df.shape[1], out.relative_to(REPO_ROOT))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("snowflake").setLevel(logging.WARNING)   # the connector logs every HTTP call at INFO
    with connect() as conn:
        paths = [export_table(conn, t) for t in TABLES]
    print("\n".join(str(p) for p in paths))


if __name__ == "__main__":
    main()
