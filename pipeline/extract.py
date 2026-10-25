"""Step 1: download one year's file from the Iowa Data Hub and check its columns.

The Hub's API is bulk-only (no filters, no pagination): each call returns a zip with
the whole year as CSV parts. So the "incremental" part happens downstream, in
transform (keep rows newer than the watermark) and load (MERGE skips known rows).
"""
from __future__ import annotations

import logging
import zipfile
from datetime import date
from pathlib import Path

import requests

from pipeline.config import API_BASE, DATASET_IDS, EXPECTED_COLUMNS, RAW_DIR

log = logging.getLogger(__name__)


class SchemaDriftError(Exception):
    """The file's columns no longer match what the pipeline expects."""


def download_year(year: int, force: bool = False) -> Path:
    """Download the zip for `year`. Reuses a copy downloaded today unless force=True,
    so a retry does not re-download 400 MB."""
    dataset_id = DATASET_IDS[year]
    out_dir = RAW_DIR / str(dataset_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"iowa_liquor_sales_{year}_{dataset_id}_rows.zip"
    if zip_path.exists() and not force:
        if date.fromtimestamp(zip_path.stat().st_mtime) == date.today():
            log.info("Reusing today's download %s (%.0f MB)", zip_path.name, zip_path.stat().st_size / 1e6)
            return zip_path
    url = f"{API_BASE}/{dataset_id}/rows.csv"
    log.info("Downloading %s", url)
    tmp = zip_path.with_suffix(".part")
    with requests.get(url, stream=True, timeout=(30, 900)) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.replace(zip_path)
    log.info("Saved %s (%.0f MB)", zip_path.name, zip_path.stat().st_size / 1e6)
    return zip_path


def unzip(zip_path: Path) -> list[Path]:
    """Extract the CSV parts next to the zip and return their paths in order."""
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if n.endswith(".csv"))
        if not names:
            raise RuntimeError(f"No CSV inside {zip_path}")
        zf.extractall(zip_path.parent, members=names)
    paths = [zip_path.parent / n for n in names]
    log.info("Extracted %d CSV part(s)", len(paths))
    return paths


def read_header(csv_path: Path) -> list[str]:
    with open(csv_path, newline="") as f:
        return f.readline().rstrip("\r\n").split(",")


def check_schema(csv_paths: list[Path], expected: list[str] = EXPECTED_COLUMNS) -> None:
    """Fail loudly if any part's header differs from the expected columns."""
    for p in csv_paths:
        header = read_header(p)
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        if missing or extra:
            raise SchemaDriftError(
                f"{p.name}: missing={missing} unexpected={extra} "
                f"(expected {len(expected)} columns, file has {len(header)})"
            )
    log.info("Schema OK: %d columns in %d file(s)", len(expected), len(csv_paths))


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Download and schema-check one year.")
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--force", action="store_true", help="re-download even if today's copy exists")
    args = ap.parse_args()
    parts = unzip(download_year(args.year, args.force))
    check_schema(parts)
    print("\n".join(str(p) for p in parts))
