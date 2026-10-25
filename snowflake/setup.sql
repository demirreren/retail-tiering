-- One-time Snowflake setup. Run this in a Snowsight worksheet as ACCOUNTADMIN.
-- Creates: a small warehouse (compute), a database, a least-privilege role,
-- and a non-human "service user" that Airflow and dbt log in as, using a key pair.
-- Replace <PUBLIC_KEY> with the contents of .secrets/snowflake_rsa_key.pub
-- (one line, without the BEGIN/END header lines).

USE ROLE ACCOUNTADMIN;

-- Compute: smallest size, suspends itself after 60s idle so the trial credit lasts.
CREATE WAREHOUSE IF NOT EXISTS RETAIL_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

-- Storage: one database, raw landing schema. dbt creates STAGING and MARTS itself.
CREATE DATABASE IF NOT EXISTS RETAIL;
CREATE SCHEMA IF NOT EXISTS RETAIL.RAW;

-- Role: only what the pipeline needs, nothing account-wide.
CREATE ROLE IF NOT EXISTS RETAIL_LOADER;
GRANT USAGE, OPERATE ON WAREHOUSE RETAIL_WH TO ROLE RETAIL_LOADER;
GRANT ALL PRIVILEGES ON DATABASE RETAIL TO ROLE RETAIL_LOADER;
GRANT ALL PRIVILEGES ON SCHEMA RETAIL.RAW TO ROLE RETAIL_LOADER;
GRANT ROLE RETAIL_LOADER TO ROLE SYSADMIN;   -- so admins can see what it builds

-- Service user: TYPE = SERVICE means no password, no MFA prompts; key pair only.
CREATE USER IF NOT EXISTS AIRFLOW_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = RETAIL_LOADER
  DEFAULT_WAREHOUSE = RETAIL_WH
  DEFAULT_NAMESPACE = RETAIL.RAW
  RSA_PUBLIC_KEY = '<PUBLIC_KEY>';
GRANT ROLE RETAIL_LOADER TO USER AIRFLOW_SVC;

-- Sanity check: should list RETAIL_LOADER.
SHOW GRANTS TO USER AIRFLOW_SVC;
