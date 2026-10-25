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
- 2025 file (id 1262): 631 MB download. 2024 file (id 1261): 672 MB download. Not profiled yet.

## Pipeline timings, verified 2026-10-25 (MacBook, local)
- Download of the 2026 zip: about 72 s
- pandas clean of all 1.81M rows, including md5 line keys: 24 s
- Incremental run with watermark 2026-09-15 (7-day overlap, cutoff 2026-09-08): 162,651 rows selected, 1,947 stores
