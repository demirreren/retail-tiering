"""check_schema: the schema-drift guard that runs before anything is loaded.

Each test writes one or two tiny CSV parts to a temp directory; nothing is downloaded.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.config import EXPECTED_COLUMNS
from pipeline.extract import SchemaDriftError, check_schema, read_header


def write_part(tmp_path: Path, name: str, columns: list[str], newline: str = "\n") -> Path:
    """A CSV part with the given header and one row of filler values."""
    p = tmp_path / name
    p.write_text(",".join(columns) + newline + ",".join("x" for _ in columns) + newline)
    return p


def test_expected_columns_are_the_23_the_hub_publishes():
    assert len(EXPECTED_COLUMNS) == 23
    assert len(set(EXPECTED_COLUMNS)) == 23


def test_passes_when_every_part_has_the_expected_columns(tmp_path):
    parts = [write_part(tmp_path, f"part{i}.csv", EXPECTED_COLUMNS) for i in range(2)]
    check_schema(parts)  # no exception is the pass


def test_column_order_does_not_matter(tmp_path):
    # transform reads columns by name, so a reordered file is still the same schema
    check_schema([write_part(tmp_path, "part.csv", list(reversed(EXPECTED_COLUMNS)))])


def test_missing_column_raises(tmp_path):
    columns = [c for c in EXPECTED_COLUMNS if c != "sales_dollars"]
    with pytest.raises(SchemaDriftError) as excinfo:
        check_schema([write_part(tmp_path, "part.csv", columns)])
    msg = str(excinfo.value)
    assert "missing=['sales_dollars']" in msg
    assert "unexpected=[]" in msg
    assert "file has 22" in msg


def test_renamed_column_raises_and_names_both_sides(tmp_path):
    columns = ["store_number" if c == "store_no" else c for c in EXPECTED_COLUMNS]
    with pytest.raises(SchemaDriftError) as excinfo:
        check_schema([write_part(tmp_path, "renamed.csv", columns)])
    msg = str(excinfo.value)
    assert msg.startswith("renamed.csv:")
    assert "missing=['store_no']" in msg
    assert "unexpected=['store_number']" in msg


def test_added_column_raises(tmp_path):
    with pytest.raises(SchemaDriftError, match=r"unexpected=\['store_region'\]"):
        check_schema([write_part(tmp_path, "part.csv", EXPECTED_COLUMNS + ["store_region"])])


def test_one_bad_part_among_good_ones_fails(tmp_path):
    good = write_part(tmp_path, "part1.csv", EXPECTED_COLUMNS)
    bad = write_part(tmp_path, "part2.csv", EXPECTED_COLUMNS[:-1])
    with pytest.raises(SchemaDriftError, match=r"^part2\.csv"):
        check_schema([good, bad])


def test_custom_expected_list_is_honoured(tmp_path):
    # The DAG's simulate_failure="schema_drift" does exactly this: expects a column the file lacks.
    part = write_part(tmp_path, "part.csv", EXPECTED_COLUMNS)
    with pytest.raises(SchemaDriftError, match=r"missing=\['store_region'\]"):
        check_schema([part], expected=EXPECTED_COLUMNS + ["store_region"])


def test_read_header_handles_windows_line_endings(tmp_path):
    part = write_part(tmp_path, "crlf.csv", EXPECTED_COLUMNS, newline="\r\n")
    assert read_header(part) == EXPECTED_COLUMNS
    check_schema([part])
