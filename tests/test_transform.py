"""line_keys and clean_part on a 5-row CSV fixture (tests/fixtures/liquor_sales_5rows.csv).

The fixture is built to exercise each rule: rows 1 and 5 are exact duplicates, row 2 has a
store id with a leading zero, a blank address and a county name padded with spaces, row 3 has
a pack value that is not a number, and row 4 has a date that does not exist.
"""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pipeline.config import EXPECTED_COLUMNS, INTEGER_COLUMNS, NUMERIC_COLUMNS
from pipeline.transform import STRING_COLUMNS, clean_part, line_keys

FIXTURE = Path(__file__).parent / "fixtures" / "liquor_sales_5rows.csv"


@pytest.fixture(scope="module")
def raw() -> pd.DataFrame:
    """The file exactly as clean_part reads it: every column as text, blanks kept as ''."""
    return pd.read_csv(FIXTURE, dtype="string[pyarrow]", keep_default_na=False)


@pytest.fixture(scope="module")
def cleaned() -> pd.DataFrame:
    return clean_part(FIXTURE)


def test_fixture_matches_the_expected_schema(raw):
    assert list(raw.columns) == EXPECTED_COLUMNS
    assert len(raw) == 5


def test_line_key_is_md5_of_the_raw_row_text(raw):
    # Same recipe as the docstring in transform.py: the raw fields joined with '|', then md5.
    rows = FIXTURE.read_text().splitlines()[1:]
    expected = [hashlib.md5("|".join(r.split(",")).encode()).hexdigest() for r in rows]
    assert line_keys(raw).tolist() == expected


def test_line_keys_are_deterministic(raw):
    first = line_keys(raw).tolist()
    again = line_keys(raw.copy()).tolist()
    assert first == again
    assert all(len(k) == 32 for k in first)


def test_exact_duplicate_rows_share_a_key_and_collapse(cleaned):
    keys = cleaned["line_key"]
    assert keys.iloc[0] == keys.iloc[4]           # row 5 repeats row 1 byte for byte
    assert keys.iloc[:4].nunique() == 4           # every distinct row gets its own key
    assert len(cleaned.drop_duplicates("line_key")) == 4


def test_output_columns(cleaned):
    assert list(cleaned.columns) == ["line_key"] + STRING_COLUMNS + ["ordered_on"] + NUMERIC_COLUMNS
    assert len(cleaned) == 5                      # clean_part keeps rows; transform_year drops the duplicates


def test_numeric_columns_are_cast(cleaned):
    for c in NUMERIC_COLUMNS:
        assert str(cleaned[c].dtype) == ("Int64" if c in INTEGER_COLUMNS else "float64"), c
    first = cleaned.iloc[0]
    assert first["pack"] == 12
    assert first["bottle_volume_ml"] == 750
    assert first["sales_bottles"] == 12
    assert first["state_bottle_cost"] == pytest.approx(7.00)
    assert first["sales_dollars"] == pytest.approx(125.88)
    assert first["sales_gallons"] == pytest.approx(2.37)


def test_unparseable_number_becomes_na(cleaned):
    assert pd.isna(cleaned.loc[2, "pack"])        # 'n/a' in the file
    assert cleaned.loc[2, "sales_dollars"] == pytest.approx(22.50)


def test_blank_strings_become_na(cleaned):
    assert pd.isna(cleaned.loc[1, "store_address"])
    assert cleaned["store_address"].notna().sum() == 4


def test_strings_are_stripped_and_ids_keep_leading_zeros(cleaned):
    assert cleaned.loc[1, "county_name"] == "STORY"       # ' STORY ' in the file
    assert cleaned.loc[1, "store_no"] == "010074"          # ids stay text, never cast to int
    assert cleaned.loc[1, "store_zip_code"] == "50010"


def test_dates_are_parsed_and_impossible_dates_become_na(cleaned):
    assert cleaned.loc[0, "ordered_on"] == date(2026, 3, 14)
    assert pd.isna(cleaned.loc[3, "ordered_on"])  # 2026-02-30 does not exist
