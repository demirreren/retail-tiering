"""Compute the correct answer for every eval question by running its SQL. Writes evals/ground_truth.json.

Run:  .venv/bin/python -m assistant.build_ground_truth
"""
from __future__ import annotations

import json
import logging

from evals.questions import QUESTIONS
from pipeline.config import REPO_ROOT
from pipeline.load import connect

OUT = REPO_ROOT / "evals" / "ground_truth.json"


def plain(v):
    import datetime as dt
    import decimal
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return v


def main() -> None:
    logging.disable(logging.WARNING)
    truth = {}
    with connect() as conn:
        for q in QUESTIONS:
            with conn.cursor() as cur:
                cur.execute(q["sql"])
                rows = [[plain(v) for v in r] for r in cur.fetchall()]
            if q["type"] in ("set",):
                value = sorted(str(r[0]) for r in rows)
            elif q["type"] == "keywords":
                value = q.get("expect_keywords") or [str(r[0]) for r in rows]
            else:
                value = rows[0][0] if rows and rows[0] else None
            truth[q["id"]] = {"type": q["type"], "question": q["question"], "truth": value}
            print(f"{q['id']} {q['type']:<12} {str(value)[:70]}")
    OUT.write_text(json.dumps(truth, indent=2))
    print(f"wrote {OUT} ({len(truth)} questions)")


if __name__ == "__main__":
    main()
