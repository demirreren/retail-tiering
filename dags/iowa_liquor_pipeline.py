"""Airflow DAG: Iowa liquor sales -> Snowflake -> dbt, once a month.

DAG = Directed Acyclic Graph: the set of tasks Airflow runs and the arrows between them
("load runs after transform"). Acyclic means nothing loops back, so it always finishes.

Task order:
  extract -> check_schema -> transform -> dq_volume -> load -> dq_freshness -> dbt_run -> dbt_test

Every task retries twice, 30 s apart. If a task still fails, alert_on_failure writes to
data/alerts.log and pops a macOS notification. To watch that on purpose, trigger the DAG
with simulate_failure = "schema_drift" or "stale_data".
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Param, dag, get_current_context, task

from pipeline import config
from pipeline import extract as extract_mod

# pandas and the Snowflake connector are imported inside the tasks, not here: the DAG
# processor re-parses this file every 30 s, and on macOS those libraries crash a forked
# parser process. Keeping the top level light also makes parsing fast.

log = logging.getLogger(__name__)


def alert_on_failure(context) -> None:
    """Failure alert: append to data/alerts.log and show a macOS notification."""
    try:
        ti = context["task_instance"]
        msg = (f"{ti.dag_id}.{ti.task_id} FAILED after {ti.try_number} attempt(s), "
               f"run {context.get('run_id')}: {context.get('exception')}")
        config.ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(config.ALERT_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{ti.task_id} failed" with title "Airflow alert" subtitle "{ti.dag_id}"'],
            check=False, timeout=10,
        )
        log.error(msg)
    except Exception as exc:  # the alert itself must never crash the DAG
        log.error("alert_on_failure could not run: %s", exc)


@dag(
    dag_id="iowa_liquor_pipeline",
    description="Download this year's Iowa liquor sales, clean, merge into Snowflake, run dbt.",
    schedule="0 9 7 * *",            # 09:00 on the 7th of each month; the Hub publishes early in the month
    start_date=datetime(2026, 10, 1),
    catchup=False,                   # do not back-run missed months on first start
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(seconds=30),
        "on_failure_callback": alert_on_failure,
    },
    params={
        "year": Param(datetime.now().year, type="integer", description="Which year's dataset to pull"),
        "simulate_failure": Param("none", enum=["none", "schema_drift", "stale_data"],
                                  description="Break a step on purpose to watch retries and the alert"),
        "freshness_max_age_days": Param(config.FRESHNESS_MAX_AGE_DAYS, type="integer"),
    },
    tags=["retail-tiering"],
)
def iowa_liquor_pipeline():

    @task
    def extract() -> list[str]:
        year = int(get_current_context()["params"]["year"])
        return [str(p) for p in extract_mod.unzip(extract_mod.download_year(year))]

    @task
    def check_schema(csv_paths: list[str]) -> list[str]:
        params = get_current_context()["params"]
        expected = list(config.EXPECTED_COLUMNS)
        if params["simulate_failure"] == "schema_drift":
            expected.append("store_region")          # pretend the publisher added a column we rely on
        extract_mod.check_schema([Path(p) for p in csv_paths], expected)
        return csv_paths

    @task
    def transform(csv_paths: list[str]) -> dict:
        from pipeline.load import connect, ensure_tables, get_watermark
        from pipeline.transform import transform_year
        ctx = get_current_context()
        year = int(ctx["params"]["year"])
        with connect() as conn:
            ensure_tables(conn)
            since = get_watermark(conn, year)
        out, stats = transform_year([Path(p) for p in csv_paths], year, ctx["run_id"], since)
        return {"parquet": str(out), "year": year, **stats}

    @task
    def dq_volume(info: dict) -> dict:
        from pipeline.load import connect
        from pipeline.quality import check_volume
        run_id = get_current_context()["run_id"]
        with connect() as conn:
            return check_volume(conn, info["year"], info["rows_in_file"], run_id)

    @task
    def load(info: dict) -> dict:
        from pipeline.load import connect, log_run, merge_parquet
        run_id = get_current_context()["run_id"]
        with connect() as conn:
            inserted = merge_parquet(conn, Path(info["parquet"]), run_id)
            log_run(conn, run_id, info["year"], info["rows_in_file"], inserted, info["max_ordered_on"], "success")
        return {"rows_inserted": inserted, "rows_in_file": info["rows_in_file"]}

    @task
    def dq_freshness(_: dict) -> dict:
        from pipeline.load import connect
        from pipeline.quality import check_freshness
        params = get_current_context()["params"]
        max_age = 1 if params["simulate_failure"] == "stale_data" else int(params["freshness_max_age_days"])
        with connect() as conn:
            return check_freshness(conn, max_age)

    dbt_run = BashOperator(task_id="dbt_run", bash_command=f"{REPO_ROOT}/scripts/dbt.sh run")
    dbt_test = BashOperator(task_id="dbt_test", bash_command=f"{REPO_ROOT}/scripts/dbt.sh test")

    info = transform(check_schema(extract()))
    volume_ok = dq_volume(info)
    loaded = load(info)
    volume_ok >> loaded                      # never load a file whose size looks wrong
    dq_freshness(loaded) >> dbt_run >> dbt_test


iowa_liquor_pipeline()
