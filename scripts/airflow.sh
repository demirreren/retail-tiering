#!/bin/zsh
# Run any airflow command with this repo's settings.
#   scripts/airflow.sh standalone                 # start scheduler + web UI at http://localhost:8080
#   scripts/airflow.sh dags list
#   scripts/airflow.sh dags test iowa_liquor_pipeline
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export AIRFLOW_HOME="$REPO/airflow_home"
export AIRFLOW__CORE__DAGS_FOLDER="$REPO/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export PYTHONPATH="$REPO"
export PATH="$REPO/.venv/bin:$PATH"   # standalone spawns sub-commands by the bare name "airflow"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES   # macOS: forked Airflow processes crash otherwise
exec "$REPO/.venv/bin/airflow" "$@"
