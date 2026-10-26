# NOTES: verified numbers only

Every number here was produced by running the code in this repo. Resume bullets
and interview answers use only what is listed here.

## Environment (verified 2026-10-25)
- Python 3.12 (Homebrew), macOS
- Airflow 3.3.2 in `.venv`
- dbt-core 1.12.6, dbt-snowflake 1.12.4 in `.venv-dbt`
- snowflake-connector-python 5.1.0, pandas 2.3.3, xgboost 3.4.1, shap 0.54.1, scikit-learn 1.9.1

## Data source
- Iowa Liquor Sales, data.iowa.gov, dataset id `m3tr-qhgy`, Socrata API

## Data source, verified 2026-10-25
- data.iowa.gov left Socrata in 2026. Liquor sales now live on the Iowa Data Hub as one dataset per year,
  bulk download only (zip of CSV parts, no filters). API: `https://idh-be.iowa.gov/api/v1/datasets/<id>/rows.csv`
- Dataset ids: 2013 = 1250 ... 2024 = 1261, 2025 = 1262, 2026 = 1263
- 2026 file (id 1263), downloaded 2026-10-25: 399 MB unzipped, 4 CSV parts, 23 columns
  - 1,814,856 rows, 2,219 distinct stores, sale dates 2026-01-01 to 2026-09-30
  - Publisher's stated unique id (invoice_id) is not unique: 2,214 repeated (invoice_id, item_no) pairs,
    of which 2,200 are real repeats (same invoice and item, different quantities) and 14 are exact duplicate rows
  - Pipeline key: md5 of the raw row text (line_key). Exact duplicates collapse: 1,814,842 rows to load
- 2025 file (id 1262): 631 MB zip, 5 CSV parts, 23 columns (schema check passes), 2,920,609 lines, 2025-01-01 to 2025-12-31
- 2024 file (id 1261): 672 MB zip, 5 CSV parts, 23 columns (schema check passes), 3,154,794 lines, 2024-01-01 to 2024-12-31

## Pipeline timings, verified 2026-10-25 (MacBook, local)
- Download of the 2026 zip: about 72 s
- pandas clean of all 1.81M rows, including md5 line keys: 24 s
- Incremental run with watermark 2026-09-15 (7-day overlap, cutoff 2026-09-08): 162,651 rows selected, 1,947 stores

## Phase 1 first end-to-end run, verified 2026-10-26 (Airflow run manual__2026-10-26T04:27:08)
- All 8 tasks succeeded, no retries, no alerts. Wall time about 130 s.
  - extract 4 s (reused the day's download), check_schema < 1 s, transform about 30 s,
    load about 50 s, dq checks < 5 s each, dbt_run 5 s, dbt_test 3 s
- Load: staged 1,814,842 rows in 1 chunk; MERGE inserted 1,814,842 new rows, 0 already present
- Freshness check: newest sale 2026-09-30, 26 days old, limit 45 days. Volume check skipped (no previous run)
- Snowflake after the run (RETAIL database):
  - RAW.LIQUOR_SALES 1,814,842 rows; RAW.LOAD_LOG 1 row (rows_in_file 1,814,856, rows_inserted 1,814,842)
  - STAGING.STG_LIQUOR_SALES view
  - MARTS.STORE_MONTH 17,936 rows = 2,219 stores x up to 9 months (2026-01 to 2026-09), total sales $301,452,235
  - MARTS.STORE_QUARTER 6,331 rows: 2026Q1 2,100 stores (1,831 active all 3 months), Q2 2,110 (1,866), Q3 2,121 (1,866)
- dbt: 3 models built (1 view, 2 tables) in 5 s; 21 tests (unique, not_null, accepted_values), 21 passed, 0 failed
