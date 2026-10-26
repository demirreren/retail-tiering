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

## Backfill and idempotency, verified 2026-10-26
- 2025 run (id 1262): success in about 190 s. Download inside Airflow 631 MB in about 70 s. File 2,920,609 rows,
  429,996 exact duplicate rows dropped, 2,490,613 rows merged, 2,243 stores.
- 2024 run (id 1261): success in about 210 s. Download 672 MB. File 3,154,794 rows,
  563,819 exact duplicate rows dropped, 2,590,975 rows merged, 2,162 stores.
- RAW.LIQUOR_SALES after backfill: 6,896,430 rows, 2,487 distinct stores, 6,896,430 distinct line keys, 2024-01-01 to 2026-09-30
- 2026 re-run (same file again): success in about 60 s. Watermark 2026-09-30, cutoff 2026-09-23 (7-day overlap):
  49,261 rows re-sent, MERGE inserted 0 (all already present). Volume check: 1,814,856 vs 1,814,856, ratio 1.0, OK.
- MARTS.STORE_MONTH 64,771 rows (2,487 stores, 2024-01 to 2026-09), total sales $1,173,501,935
- MARTS.STORE_QUARTER 22,838 rows over 11 quarters; about 1,770 to 1,870 stores per quarter are active all 3 months

## Duplicate rows in the published files (root cause), verified 2026-10-26 two ways
- Checked with my md5 line key AND with plain `sort | uniq -d` on the raw CSV (2025: 429,986 distinct lines appear
  more than once; 429,996 extra rows in total because a few appear 3+ times).
- The duplicates sit across CSV parts, not within one part (2025: 429,989 of 429,996 across parts; 2024: all 563,819).
- They are whole months doubled: 2025-09 and 2025-10 fully doubled (plus part of 2025-08); 2024-11 and 2024-12 fully doubled
  (plus part of 2024-10). 2026 has only 14 duplicate rows.
- Impact if loaded naively: 2025 row count overstated by 17.3% (429,996 / 2,490,613), 2024 by 21.8% (563,819 / 2,590,975);
  as a share of the published file, 14.7% and 17.9% of rows are repeats.

## Tiering (Phase 2), verified 2026-10-26 (tiering/tiers.py)
- Score = 0.4 sales + 0.2 growth + 0.2 consistency + 0.2 product mix, each a percentile rank within the quarter.
  Tiers: A = top 20%, B = next 30%, C = bottom 50%. Only stores active all 3 months are tiered.
- 22,838 store-quarters, 20,161 tiered, 2,487 stores, 11 quarters (2024Q1 to 2026Q3)
- Latest quarter (2026Q3): A 373, B 560, C 933 stores. Tier A = 66.7% of sales, B = 21.7%, C = 11.6%
- Back-test: 17,392 consecutive store-quarter pairs; 68.7% keep their tier next quarter (range 65.4% to 75.7% by quarter)
- Transition matrix (this quarter -> next): A->A 66.5%, A->B 30.7%, A->C 2.8%; B->A 20.7%, B->B 51.2%, B->C 28.1%;
  C->A 1.2%, C->B 17.9%, C->C 80.9%
