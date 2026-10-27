"""check_read_only: the statement check that runs before any SQL reaches Snowflake.

These are the cases from the module's original offline self-test, plus the reason each
rejection gives. Pure function, so no connection is made.
"""
from __future__ import annotations

import pytest

from assistant.sql_tool import QueryRejected, check_read_only

ACCEPTED = [
    "SELECT 1",
    "with q as (select 1) select * from q",
    "SELECT * FROM store_tiers WHERE tier = 'A';",
    "SELECT store_id FROM store_tiers WHERE next_change = 'drop'",
    "select * from t where name = 'DELETE ME; DROP IT'",
    "select county_name, sum(sales_dollars) from store_quarter group by 1 order by 2 desc limit 5",
]

REJECTED = [  # (sql, fragment of the reason the error message must give)
    ("DELETE FROM store_tiers", "Only SELECT"),
    ("UPDATE t SET a=1", "Only SELECT"),
    ("CREATE TABLE t AS SELECT 1", "Only SELECT"),
    ("SHOW TABLES", "Only SELECT"),
    ("use role retail_loader", "Only SELECT"),
    ("SELECT 1; DROP TABLE x", "Only one statement"),
    ("select 1 union select 1 from t; delete from t", "Only one statement"),
    ("select 1 -- comment", "Comments are not allowed"),
    ("select * from t where a = 1 /* x */", "Comments are not allowed"),
    ("select * from t where a = 'x", "Unbalanced quotes"),
    ("with q as (delete from t returning 1) select * from q", "Keyword not allowed in a read-only query: DELETE"),
    ("select * from t where exists (select 1 from u) and merge_flag in (select 1 from t2 merge into x)", "MERGE"),
    ("", "Empty query"),
    ("   ;  ", "Empty query"),
]


@pytest.mark.parametrize("sql", ACCEPTED)
def test_read_only_queries_are_accepted(sql):
    assert check_read_only(sql)


@pytest.mark.parametrize("sql,reason", REJECTED)
def test_everything_else_is_rejected_with_a_reason(sql, reason):
    with pytest.raises(QueryRejected, match=reason):
        check_read_only(sql)


def test_returns_the_query_without_trailing_semicolon_or_padding():
    assert check_read_only("  SELECT 1;  ") == "SELECT 1"
    assert check_read_only("SELECT 1") == "SELECT 1"


def test_keywords_inside_string_literals_do_not_count():
    # The first live eval question hit this: next_change = 'drop' must not read as DROP.
    sql = "SELECT store_id FROM store_tiers WHERE next_change = 'drop' AND tier = 'A'"
    assert check_read_only(sql) == sql


def test_semicolon_inside_a_literal_is_fine_but_outside_is_not():
    assert check_read_only("select 1 from t where x = 'a;b'")
    with pytest.raises(QueryRejected, match="Only one statement"):
        check_read_only("select 1 from t where x = 'a'; select 2")
