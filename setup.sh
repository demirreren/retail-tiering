#!/bin/zsh
# Reproduce the two Python environments. Run once from the repo root.
set -e
PY=/opt/homebrew/bin/python3.12   # Airflow and dbt both support 3.12
AF_VER=3.3.2

# 1) Airflow + analysis libs. Airflow publishes a "constraints" file: a list of
#    exact versions it was tested with, so pip does not pick incompatible ones.
#    One pin is dropped from it: the file pins pandas 3, but the Snowflake connector's
#    pandas extra still requires pandas < 3, so with that line pip refuses to install
#    anything. Without it pip picks the newest pandas 2.x; every other pin still applies.
CONSTRAINTS="$(mktemp)"
curl -sSL "https://raw.githubusercontent.com/apache/airflow/constraints-$AF_VER/constraints-3.12.txt" \
  | grep -v '^pandas==' > "$CONSTRAINTS"
$PY -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt --constraint "$CONSTRAINTS"

# 2) dbt in its own venv (see requirements-dbt.txt for why).
$PY -m venv .venv-dbt
.venv-dbt/bin/pip install --upgrade pip
.venv-dbt/bin/pip install -r requirements-dbt.txt

# 3) XGBoost on macOS needs the OpenMP runtime.
command -v brew >/dev/null && brew list libomp >/dev/null 2>&1 || brew install libomp

echo "Done. Airflow: $(.venv/bin/airflow version)  dbt: $(.venv-dbt/bin/dbt --version | grep installed)"
