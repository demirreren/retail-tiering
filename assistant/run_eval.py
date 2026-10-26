"""Run every eval question through the assistant and grade it against the SQL ground truth.

    .venv/bin/python -m assistant.run_eval            # all questions
    .venv/bin/python -m assistant.run_eval q05 q31    # a few, by id

Writes evals/results.json (everything, including the SQL the assistant ran) and evals/results.md.
Grading is deterministic: no model judges another model here.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime

from assistant.agent import Assistant
from evals.questions import QUESTIONS
from pipeline.config import REPO_ROOT

EVALS = REPO_ROOT / "evals"


def norm_id(s) -> str:
    s = str(s).strip()
    return s.lstrip("0") or "0"


def close(n: float, truth: float, rel: float = 0.01, abs_floor: float = 0.5) -> bool:
    return abs(n - truth) <= max(rel * abs(truth), abs_floor)


def grade(qtype: str, truth, parsed: dict, text: str, trace: list) -> tuple[bool, str]:
    nums = parsed.get("numbers", [])
    ids = {norm_id(x) for x in parsed.get("store_ids", [])}
    na = bool(parsed.get("could_not_answer"))
    low = (text or "").lower()
    if qtype == "count":
        return (any(abs(n - float(truth)) < 1e-6 for n in nums), f"expected {truth}, got {nums}")
    if qtype == "number":
        return (any(close(n, float(truth)) for n in nums), f"expected ~{truth}, got {nums}")
    if qtype == "percent":
        t = float(truth)
        ok = any(abs(n - t) <= 0.006 or abs(n - 100 * t) <= 0.6 for n in nums)
        return (ok, f"expected {t:.4f} (or {100*t:.1f}%), got {nums}")
    if qtype == "set":
        want = {norm_id(x) for x in truth}
        ordered = [norm_id(x) for x in parsed.get("store_ids", [])]
        if not ordered:  # fall back to ids mentioned in the prose
            ordered = [norm_id(x) for x in re.findall(r"\b\d{3,6}\b", text or "") if norm_id(x) in want]
        if len(want) == 1:   # one right answer: the first store named must be it (runners-up as context are fine)
            return (bool(ordered) and ordered[0] in want, f"expected {sorted(want)}, first named {ordered[:1]}")
        return (set(ordered) == want, f"expected {sorted(want)}, got {sorted(set(ordered))}")
    if qtype == "keywords":
        missing = [k for k in truth if k.lower() not in low]
        return (not missing, f"missing {missing}" if missing else "all keywords present")
    if qtype == "yesno":
        m = re.search(r"\b(yes|no)\b", low)
        return (bool(m) and m.group(1) == str(truth).lower(), f"expected {truth}, answer starts {m.group(1) if m else None}")
    if qtype == "unanswerable":
        return (na, "could_not_answer=" + str(na))
    if qtype == "zero_or_na":
        return (na or any(abs(n) < 1e-6 for n in nums), f"could_not_answer={na}, numbers={nums}")
    if qtype == "refuse":
        attempted = any("rejected" in t for t in trace)
        return (na and not attempted, f"could_not_answer={na}, write_attempted={attempted}")
    return (False, f"unknown type {qtype}")


def main() -> None:
    logging.disable(logging.WARNING)
    truth = json.loads((EVALS / "ground_truth.json").read_text())
    only = set(sys.argv[1:])
    qs = [q for q in QUESTIONS if not only or q["id"] in only]
    bot = Assistant()
    results = []
    try:
        for q in qs:
            a = bot.ask(q["question"])
            ok, why = grade(q["type"], truth[q["id"]]["truth"], a.parsed, a.text, a.sql_trace)
            if a.error:
                ok, why = False, a.error
            results.append({
                "id": q["id"], "type": q["type"], "question": q["question"], "truth": truth[q["id"]]["truth"],
                "passed": ok, "why": why, "answer": a.text, "parsed": a.parsed, "sql_trace": a.sql_trace,
                "tool_calls": a.tool_calls, "seconds": a.seconds, "cost_usd": round(a.cost_usd, 4),
                "tokens": {"input": a.input_tokens, "output": a.output_tokens,
                           "cache_read": a.cache_read_tokens, "cache_write": a.cache_write_tokens},
                "stop_reason": a.stop_reason, "error": a.error,
            })
            mark = "PASS" if ok else "FAIL"
            print(f"{q['id']} {mark} {q['type']:<12} {a.tool_calls} calls {a.seconds:>5}s ${a.cost_usd:.3f}  {why[:90]}")
    finally:
        bot.close()
    n = len(results); passed = sum(r["passed"] for r in results)
    cost = sum(r["cost_usd"] for r in results); secs = sum(r["seconds"] for r in results)
    summary = {"run_at": datetime.now().isoformat(timespec="seconds"), "model": bot.model, "questions": n,
               "passed": passed, "pass_rate": round(passed / n, 4) if n else None,
               "total_cost_usd": round(cost, 3), "total_seconds": round(secs, 1),
               "avg_tool_calls": round(sum(r["tool_calls"] for r in results) / n, 2) if n else None}
    (EVALS / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    lines = [f"# Eval results ({summary['run_at']}, {summary['model']})", "",
             f"**{passed} / {n} passed ({summary['pass_rate']:.0%})**, {summary['avg_tool_calls']} tool calls per question on average, "
             f"{secs:.0f} s total, about ${cost:.2f} in API cost.", "",
             "| id | type | result | calls | s | note |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['type']} | {'pass' if r['passed'] else 'FAIL'} | {r['tool_calls']} | {r['seconds']} | {r['why'][:80].replace('|', '/')} |")
    (EVALS / "results.md").write_text("\n".join(lines) + "\n")
    print(f"\n{passed}/{n} passed ({summary['pass_rate']:.0%}); ${cost:.2f}; {secs:.0f}s. Wrote evals/results.json and evals/results.md")


if __name__ == "__main__":
    main()
