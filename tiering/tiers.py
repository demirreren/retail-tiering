"""Store tiering: a weighted composite score per store per quarter, cut into tiers A/B/C,
plus a back-test of how stable the tiers are from one quarter to the next.

How the score works, in plain words:
  1. Four inputs per store-quarter: sales, growth vs the previous quarter, consistency
     (how even the three months were), and product mix (distinct categories).
  2. Each input is turned into a percentile rank within the quarter (0 = lowest store,
     1 = highest). Ranks put dollars, percentages and counts on one scale and are not
     thrown off by a few giant stores.
  3. Composite = 0.4*sales + 0.2*growth + 0.2*consistency + 0.2*mix.
  4. Tiers by composite within the quarter: A = top 20%, B = next 30%, C = bottom 50%.
Only stores active all three months of a quarter are tiered; partial quarters get no tier.

Run:  .venv/bin/python -m tiering.tiers          (writes MARTS.STORE_TIERS and a CSV)
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from snowflake.connector.pandas_tools import write_pandas

from pipeline.config import PROCESSED_DIR, snowflake_settings
from pipeline.load import connect

log = logging.getLogger(__name__)

WEIGHTS = {"sales": 0.40, "growth": 0.20, "consistency": 0.20, "mix": 0.20}
TIER_CUTS = {"A": 0.20, "B": 0.50}        # top 20% -> A, next 30% -> B, rest -> C
TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def fetch_store_quarter(conn) -> pd.DataFrame:
    db = snowflake_settings()["database"]
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {db}.MARTS.STORE_QUARTER ORDER BY STORE_ID, QUARTER_START")
        df = cur.fetch_pandas_all()
    df.columns = [c.lower() for c in df.columns]
    df["quarter_start"] = pd.to_datetime(df["quarter_start"])
    for c in ["sales_dollars", "monthly_sales_cv", "distinct_categories", "active_months"]:
        df[c] = pd.to_numeric(df[c])
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["store_id", "quarter_start"]).copy()
    prev = df.groupby("store_id")["sales_dollars"].shift(1)
    prev_q = df.groupby("store_id")["quarter_start"].shift(1)
    consecutive = (df["quarter_start"] - prev_q).dt.days.between(85, 95)   # previous row is the prior quarter
    df["growth"] = (df["sales_dollars"] / prev - 1).where(consecutive)
    df["consistency"] = -df["monthly_sales_cv"]       # lower variation = better, so flip the sign
    df["mix"] = df["distinct_categories"]
    df["sales"] = df["sales_dollars"]
    return df


def score_and_tier(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["eligible"] = df["active_months"] == 3
    elig = df[df["eligible"]].copy()
    for feat in WEIGHTS:
        # percentile rank within each quarter; unknown growth (first quarter seen) gets the middle rank
        elig[f"{feat}_rank"] = elig.groupby("quarter_start")[feat].rank(pct=True).fillna(0.5)
    elig["composite"] = sum(w * elig[f"{f}_rank"] for f, w in WEIGHTS.items())
    pos = elig.groupby("quarter_start")["composite"].rank(ascending=False, pct=True)   # 0 = best
    elig["tier"] = pd.cut(pos, [0, TIER_CUTS["A"], TIER_CUTS["B"], 1.0], labels=["A", "B", "C"]).astype(str)
    cols = [f"{f}_rank" for f in WEIGHTS] + ["composite", "tier"]
    df = df.join(elig[cols])
    df["tier"] = df["tier"].fillna("")        # partial quarters: not tiered
    return df


def add_transitions(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["store_id", "quarter_start"]).copy()
    g = df.groupby("store_id")
    nxt_q = g["quarter_start"].shift(-1)
    consecutive = (nxt_q - df["quarter_start"]).dt.days.between(85, 95)
    df["next_tier"] = g["tier"].shift(-1).where(consecutive, "")
    df["prev_tier"] = g["tier"].shift(1).where(
        (df["quarter_start"] - g["quarter_start"].shift(1)).dt.days.between(85, 95), "")
    both = (df["tier"] != "") & (df["next_tier"] != "")
    cur = df["tier"].map(TIER_ORDER); nxt = df["next_tier"].map(TIER_ORDER)
    df["next_change"] = ""
    df.loc[both & (nxt > cur), "next_change"] = "drop"
    df.loc[both & (nxt == cur), "next_change"] = "same"
    df.loc[both & (nxt < cur), "next_change"] = "up"
    return df


def backtest(df: pd.DataFrame) -> dict:
    """How often does a store keep its tier next quarter? Plus the full transition matrix."""
    t = df[df["next_change"] != ""]
    stability = (t["next_change"] == "same").mean()
    by_q = t.groupby("quarter_start")["next_change"].apply(lambda s: (s == "same").mean())
    matrix = pd.crosstab(t["tier"], t["next_tier"], normalize="index").round(3)
    return {"pairs": len(t), "stability": round(float(stability), 4),
            "stability_by_quarter": by_q.round(3).to_dict(), "transition_matrix": matrix}


def write_results(conn, df: pd.DataFrame) -> None:
    out = df.copy()
    out["quarter_start"] = out["quarter_start"].dt.date
    out.columns = [c.upper() for c in out.columns]
    db = snowflake_settings()["database"]
    write_pandas(conn, out, "STORE_TIERS", database=db, schema="MARTS",
                 auto_create_table=True, overwrite=True, quote_identifiers=False)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(PROCESSED_DIR / "store_tiers.csv", index=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with connect() as conn:
        sq = fetch_store_quarter(conn)
        df = add_transitions(score_and_tier(add_features(sq)))
        write_results(conn, df)
    tiered = df[df["tier"] != ""]
    print(f"store-quarters: {len(df):,}  tiered: {len(tiered):,}  stores: {df['store_id'].nunique():,}  "
          f"quarters: {df['quarter_start'].nunique()} ({df['quarter_start'].min().date()} to {df['quarter_start'].max().date()})")
    print("tier counts per quarter:")
    print(pd.crosstab(tiered["quarter_start"].dt.date, tiered["tier"]).to_string())
    print("share of sales by tier (latest quarter):")
    last = tiered[tiered["quarter_start"] == tiered["quarter_start"].max()]
    print((last.groupby("tier")["sales_dollars"].sum() / last["sales_dollars"].sum()).round(3).to_string())
    bt = backtest(df)
    print(f"back-test: {bt['pairs']:,} store-quarter pairs, {bt['stability']:.1%} keep their tier next quarter")
    print("stability by quarter:", {str(k.date()): v for k, v in bt["stability_by_quarter"].items()})
    print("transition matrix (row = this quarter, col = next quarter):")
    print(bt["transition_matrix"].to_string())


if __name__ == "__main__":
    main()
