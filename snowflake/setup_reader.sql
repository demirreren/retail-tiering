-- Read-only access for the question-answering assistant. Run in Snowsight as ACCOUNTADMIN.
-- Why a separate user and not just a role: Snowflake can activate all of a user's roles at once
-- ("secondary roles"), so a user that also holds the loader role could still write. A user that
-- holds ONLY the reader role cannot, whatever SQL the assistant generates.
-- Replace <PUBLIC_KEY> with the contents of .secrets/assistant_rsa_key.pub (one line, no header lines).

USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS RETAIL_READER;
GRANT USAGE ON WAREHOUSE RETAIL_WH TO ROLE RETAIL_READER;
GRANT USAGE ON DATABASE RETAIL TO ROLE RETAIL_READER;
GRANT USAGE ON SCHEMA RETAIL.MARTS TO ROLE RETAIL_READER;
GRANT SELECT ON ALL TABLES IN SCHEMA RETAIL.MARTS TO ROLE RETAIL_READER;
GRANT SELECT ON FUTURE TABLES IN SCHEMA RETAIL.MARTS TO ROLE RETAIL_READER;   -- tables dbt rebuilds later
GRANT ROLE RETAIL_READER TO ROLE SYSADMIN;

CREATE USER IF NOT EXISTS ASSISTANT_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = RETAIL_READER
  DEFAULT_WAREHOUSE = RETAIL_WH
  DEFAULT_NAMESPACE = RETAIL.MARTS
  RSA_PUBLIC_KEY = '<PUBLIC_KEY>';
GRANT ROLE RETAIL_READER TO USER ASSISTANT_SVC;

-- Sanity check: should list exactly one role, RETAIL_READER.
SHOW GRANTS TO USER ASSISTANT_SVC;
