#!/bin/zsh
# Run dbt with secrets loaded from .env. Usage: scripts/dbt.sh debug | run | test
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
set -a; source "$REPO/.env"; set +a
case "$SNOWFLAKE_PRIVATE_KEY_PATH" in
  /*) ;;
  *) export SNOWFLAKE_PRIVATE_KEY_PATH="$REPO/$SNOWFLAKE_PRIVATE_KEY_PATH" ;;
esac
cd "$REPO/dbt"
exec "$REPO/.venv-dbt/bin/dbt" "$@"
