"""Tier-drop model: which tier A and B stores will drop a tier next quarter, and why.

Plain words:
  - Each row is one store in one quarter. The label is 1 if the store's tier next quarter
    is lower (A->B, A->C, B->C), else 0. Tier C stores cannot drop, so they are excluded.
  - Features are things known at the end of that quarter: the four rank inputs, the
    composite, how far the store sits above the bottom of its tier (margin), raw growth,
    month-to-month consistency, product breadth, and what happened last quarter (momentum).
  - XGBoost = gradient-boosted decision trees: many small trees, each one correcting the
    mistakes of the ones before. Strong on tabular data, handles missing values natively.
  - The split is by time, never random: train on older quarters, test on the newest labelled
    ones, so the score reflects predicting the future, not memorising it.
  - SHAP (SHapley Additive exPlanations) splits one prediction into a contribution per
    feature, so for any store we can say "risk is high mainly because X and Y".

Run:  .venv/bin/python -m tiering.predict_drop
Writes MARTS.TIER_DROP_RISK (latest quarter, one row per A/B store) and two PNGs in reports/.
"""
from __future__ import annotations

import logging

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shap
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import average_precision_score, roc_auc_score
from snowflake.connector.pandas_tools import write_pandas

from pipeline.config import REPO_ROOT, snowflake_settings
from pipeline.load import connect

log = logging.getLogger(__name__)
REPORTS = REPO_ROOT / "reports"
FEATURES = ["sales_rank", "growth_rank", "consistency_rank", "mix_rank", "composite", "margin",
            "growth", "monthly_sales_cv", "distinct_categories", "distinct_items", "invoices",
            "log_sales", "is_tier_a", "momentum", "quarter"]
PLAIN = {  # how to say each feature out loud
    "sales_rank": "sales rank in quarter", "growth_rank": "growth rank", "consistency_rank": "consistency rank",
    "mix_rank": "product-mix rank", "composite": "composite score", "margin": "cushion above tier floor",
    "growth": "sales growth vs last quarter", "monthly_sales_cv": "month-to-month swing",
    "distinct_categories": "categories carried", "distinct_items": "items carried", "invoices": "number of orders",
    "log_sales": "sales size (log)", "is_tier_a": "is tier A", "momentum": "last quarter's tier move",
    "quarter": "quarter of year",
}


def load_tiers(conn) -> pd.DataFrame:
    db = snowflake_settings()["database"]
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {db}.MARTS.STORE_TIERS")
        df = cur.fetch_pandas_all()
    df.columns = [c.lower() for c in df.columns]
    df["quarter_start"] = pd.to_datetime(df["quarter_start"])
    for c in ["sales_rank", "growth_rank", "consistency_rank", "mix_rank", "composite", "growth",
              "monthly_sales_cv", "distinct_categories", "distinct_items", "invoices", "sales_dollars"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ["tier", "next_tier", "prev_tier", "next_change"]:
        df[c] = df[c].fillna("").astype(str)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["tier"].isin(["A", "B"])].copy()
    floor = d.groupby(["quarter_start", "tier"])["composite"].transform("min")
    d["margin"] = d["composite"] - floor
    d["log_sales"] = np.log1p(d["sales_dollars"])
    d["is_tier_a"] = (d["tier"] == "A").astype(int)
    order = {"A": 0, "B": 1, "C": 2}
    prev = d["prev_tier"].map(order)
    cur = d["tier"].map(order)
    d["momentum"] = prev - cur          # +1 = moved up into this tier, -1 = fell into it, 0 = stayed, NaN = new
    d["quarter"] = d["quarter_start"].dt.quarter
    d["label"] = (d["next_change"] == "drop").astype(int)
    d["has_label"] = d["next_change"] != ""
    return d


def time_split(d: pd.DataFrame, n_test_quarters: int = 2):
    labelled = d[d["has_label"]]
    quarters = sorted(labelled["quarter_start"].unique())
    test_q = quarters[-n_test_quarters:]
    train = labelled[~labelled["quarter_start"].isin(test_q)]
    test = labelled[labelled["quarter_start"].isin(test_q)]
    return train, test, test_q


def precision_at_top(y, p, frac=0.10) -> float:
    k = max(1, int(len(p) * frac))
    idx = np.argsort(-p)[:k]
    return float(np.asarray(y)[idx].mean())


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    REPORTS.mkdir(exist_ok=True)
    with connect() as conn:
        tiers = load_tiers(conn)
        d = build_features(tiers)
        train, test, test_q = time_split(d)
        print(f"rows: train {len(train):,} (quarters {train['quarter_start'].min().date()} to "
              f"{train['quarter_start'].max().date()}), test {len(test):,} (quarters {[str(q.date()) for q in test_q]})")
        print(f"drop rate: train {train['label'].mean():.1%}, test {test['label'].mean():.1%}")

        pos_weight = (train["label"] == 0).sum() / max(1, (train["label"] == 1).sum())
        model = xgb.XGBClassifier(
            n_estimators=400, max_depth=4, learning_rate=0.03, subsample=0.8, colsample_bytree=0.8,
            min_child_weight=5, reg_lambda=2.0, scale_pos_weight=pos_weight, eval_metric="aucpr", random_state=42,
        )
        model.fit(train[FEATURES], train["label"])
        p = model.predict_proba(test[FEATURES])[:, 1]
        y = test["label"].values

        # Baselines: (1) "closest to the tier floor drops" rule, (2) logistic regression on the same features.
        base_margin = -test["margin"].values
        lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, class_weight="balanced"))
        lr.fit(train[FEATURES].fillna(0), train["label"])
        p_lr = lr.predict_proba(test[FEATURES].fillna(0))[:, 1]

        print("\nmodel                  AUC    avg precision   precision@top10%")
        for name, pred in [("margin-only rule", base_margin), ("logistic regression", p_lr), ("XGBoost", p)]:
            print(f"{name:<20} {roc_auc_score(y, pred):6.3f}   {average_precision_score(y, pred):6.3f}"
                  f"          {precision_at_top(y, pred, 0.10):6.3f}")
        print(f"(test drop rate = {y.mean():.3f}: what a random top-10% pick would score)")

        # Global explanation: mean |SHAP| per feature on the test set.
        explainer = shap.TreeExplainer(model)
        sv = explainer.shap_values(test[FEATURES])
        imp = pd.Series(np.abs(sv).mean(axis=0), index=FEATURES).sort_values(ascending=False)
        print("\nmean |SHAP| on the test set (average push on the prediction, in log-odds):")
        for f, v in imp.head(8).items():
            print(f"   {PLAIN[f]:<32} {v:.3f}")
        shap.summary_plot(sv, test[FEATURES].rename(columns=PLAIN), show=False, max_display=12)
        plt.tight_layout(); plt.savefig(REPORTS / "shap_summary.png", dpi=130); plt.close()

        # Score the newest quarter (no label yet): who is at risk of dropping next quarter?
        latest_q = d["quarter_start"].max()
        latest = d[d["quarter_start"] == latest_q].copy()
        latest["p_drop"] = model.predict_proba(latest[FEATURES])[:, 1]
        sv_latest = explainer.shap_values(latest[FEATURES])
        latest["top_reasons"] = [
            "; ".join(f"{PLAIN[f]} ({v:+.2f})" for f, v in pd.Series(sv_latest[i], index=FEATURES).sort_values(ascending=False).head(3).items())
            for i in range(len(latest))
        ]
        out = latest[["store_quarter_key", "store_id", "store_name", "store_city", "county_name", "quarter_start",
                      "tier", "composite", "margin", "p_drop", "top_reasons"]].copy()
        out["quarter_start"] = out["quarter_start"].dt.date
        out.columns = [c.upper() for c in out.columns]
        write_pandas(conn, out, "TIER_DROP_RISK", database=snowflake_settings()["database"], schema="MARTS",
                     auto_create_table=True, overwrite=True, quote_identifiers=False)
        print(f"\nscored {len(latest):,} A/B stores for {latest_q.date()} -> MARTS.TIER_DROP_RISK; "
              f"{int((latest['p_drop'] >= 0.5).sum()):,} flagged at p >= 0.5")

        # One specific store, explained: the highest-risk tier A store in Polk County (Des Moines).
        polk = latest[(latest["county_name"].str.upper() == "POLK") & (latest["tier"] == "A")]
        pool = polk if len(polk) else latest
        pick = pool.sort_values("p_drop", ascending=False).iloc[0]
        i = latest.index.get_loc(pick.name)
        contrib = pd.Series(sv_latest[i], index=FEATURES)
        ev = explainer.expected_value
        base = float(ev if np.ndim(ev) == 0 else np.asarray(ev).ravel()[0])
        print(f"\nexample store: {pick['store_name']} (store {pick['store_id']}, {pick['store_city']}, {pick['county_name']} county), "
              f"tier {pick['tier']}, p(drop next quarter) = {pick['p_drop']:.2f}")
        print(f"   model baseline (log-odds) {base:+.2f}; contributions pushing risk up (+) or down (-):")
        for f, v in contrib.reindex(contrib.abs().sort_values(ascending=False).index).head(6).items():
            print(f"   {PLAIN[f]:<32} {v:+.2f}   (value {pick[f]:.3g})")
        shap.plots.waterfall(
            shap.Explanation(values=sv_latest[i], base_values=base, data=latest[FEATURES].iloc[i].values,
                             feature_names=[PLAIN[f] for f in FEATURES]), show=False, max_display=10)
        plt.title(f"Store {pick['store_id']}, {pick['store_city']}: tier-drop risk, feature by feature", fontsize=10)
        plt.tight_layout(); plt.savefig(REPORTS / f"shap_store_{pick['store_id']}.png", dpi=130); plt.close()
        print(f"   saved reports/shap_summary.png and reports/shap_store_{pick['store_id']}.png")


if __name__ == "__main__":
    main()
