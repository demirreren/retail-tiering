"""The assistant: Claude answers a question by writing SQL and running it through the read-only tool.

How it works (tool calling): the model is given one tool, run_sql, with a description of what
it does. When the model decides it needs data, it replies with a tool call instead of text; the
SDK's tool runner executes our Python function, hands the rows back to the model, and repeats
until the model answers in text. We never let the model touch Snowflake directly.

Every answer ends with a small JSON block (answer, numbers, store_ids, could_not_answer) so the
eval can grade it mechanically instead of by eye.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import anthropic
from anthropic import beta_tool
from dotenv import dotenv_values

from assistant.sql_tool import QueryRejected, connect_reader, run_sql, schema_summary
from pipeline.config import REPO_ROOT

MODEL = "claude-opus-5-5"
PRICE = {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}   # $ per million tokens
MAX_TOOL_CALLS = 8

TABLE_NOTES = """
What the tables mean (one row per store per period):
- STORE_QUARTER: sales totals per store per quarter. quarter_start is the first day of the quarter
  (Q1 = YYYY-01-01, Q2 = YYYY-04-01, Q3 = YYYY-07-01, Q4 = YYYY-10-01). active_months is how many of
  the 3 months the store traded.
- STORE_MONTH: same, per month (sale_month = first day of the month).
- STORE_TIERS: the tier score and tier per store per quarter. tier is 'A', 'B', 'C', or empty when the
  store was not tiered (not active all 3 months). next_tier / next_change describe what happened the
  FOLLOWING quarter: next_change is 'drop', 'same', 'up', or empty when there is no following quarter.
  So "stores that dropped a tier between Q2 and Q3" = rows with quarter_start = Q2 and next_change = 'drop'.
  prev_tier is the tier in the previous quarter. growth is sales growth vs the previous quarter (0.1 = +10%).
- TIER_DROP_RISK: for the latest quarter only, every tier A and B store with p_drop (the model's
  probability that the store drops a tier next quarter) and top_reasons (the main drivers, as text).
Data covers 2024-01-01 to 2026-09-30: quarters 2024 Q1 through 2026 Q3. The latest quarter is 2026 Q3
(quarter_start 2026-07-01). There is nothing before 2024.
County and city names are stored in upper case ('POLK', 'DES MOINES'). Store ids are strings and some
have leading zeros ('010074'): return them exactly as stored.
"""

SYSTEM_RULES = """You are an analyst's assistant for Iowa liquor sales data in Snowflake. Answer questions by
writing SQL and running it with the run_sql tool. Rules:
1. Every number and every store you mention must come from a query you ran. Never answer from memory.
2. You can only read. The tool accepts a single SELECT. If asked to change, delete, insert, or update
   data, do not try: explain that you only have read access, and set could_not_answer to true.
3. If the data cannot answer the question (a period or place not in the data, a column that does not
   exist), say so plainly and set could_not_answer to true. Do not guess or estimate.
4. Keep queries small and specific. Aggregate in SQL rather than pulling many rows. Use fully
   qualified names like RETAIL.MARTS.STORE_TIERS.
5. Answer in two or three plain sentences, then end with exactly one JSON block in this form:
```json
{"answer": "<your plain-language answer>", "numbers": [<key figures as plain numbers, no $ or commas>],
 "store_ids": ["<ids of stores you named, exactly as stored>"], "could_not_answer": false}
```
For a percentage, put the fraction (0.667) in numbers. For a yes/no question, start the answer with Yes or No.
"""


@dataclass
class Answer:
    question: str
    text: str
    parsed: dict
    sql_trace: list = field(default_factory=list)
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    seconds: float = 0.0
    stop_reason: str = ""
    error: str = ""

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * PRICE["input"] + self.output_tokens * PRICE["output"]
                + self.cache_read_tokens * PRICE["cache_read"] + self.cache_write_tokens * PRICE["cache_write"]) / 1e6


def parse_final(text: str) -> dict:
    """Pull the JSON block out of the answer; fall back to numbers found in the prose."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    for raw in reversed(blocks):
        try:
            d = json.loads(raw)
            d.setdefault("numbers", []); d.setdefault("store_ids", []); d.setdefault("could_not_answer", False)
            d["numbers"] = [float(n) for n in d["numbers"] if isinstance(n, (int, float))]
            d["store_ids"] = [str(s) for s in d["store_ids"]]
            return d
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    nums = [float(n.replace(",", "")) for n in re.findall(r"-?\d[\d,]*\.?\d*", text)]
    return {"answer": text.strip(), "numbers": nums, "store_ids": [], "could_not_answer": False, "unparsed": True}


class Assistant:
    def __init__(self, model: str = MODEL):
        env = dotenv_values(REPO_ROOT / ".env")
        key = env.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is empty in .env")
        # base_url is pinned so a proxy variable in the shell cannot redirect the project's calls.
        self.client = anthropic.Anthropic(api_key=key, base_url=env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com")
        self.model = model
        self.conn = connect_reader()
        self.system = SYSTEM_RULES + "\nTables:\n" + schema_summary(self.conn) + "\n" + TABLE_NOTES

    def close(self) -> None:
        self.conn.close()

    def ask(self, question: str) -> Answer:
        trace: list[dict] = []
        conn = self.conn

        @beta_tool
        def run_sql_tool(sql: str) -> str:
            """Run one read-only SQL SELECT against the RETAIL.MARTS tables and get up to 200 rows back as JSON.

            Args:
                sql: A single SELECT or WITH ... SELECT statement. No semicolons, no comments, no writes.
            """
            entry = {"sql": sql}
            try:
                result = run_sql(conn, sql)
                entry["rows"] = result["row_count"]
                out = json.dumps(result, default=str)
            except QueryRejected as e:
                entry["rejected"] = str(e)
                out = f"QUERY REJECTED: {e}"
            except Exception as e:  # Snowflake errors: let the model read the message and fix the query
                entry["error"] = str(e)[:300]
                out = f"QUERY ERROR: {str(e)[:500]}"
            trace.append(entry)
            return out

        started = time.time()
        ans = Answer(question=question, text="", parsed={}, sql_trace=trace)
        try:
            runner = self.client.beta.messages.tool_runner(
                model=self.model,
                max_tokens=4000,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                tools=[run_sql_tool],
                messages=[{"role": "user", "content": question}],
                max_iterations=MAX_TOOL_CALLS + 1,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",            # if a safety classifier declines, the API retries on a fallback model
            )
            last = None
            for message in runner:
                last = message
                u = message.usage
                ans.input_tokens += u.input_tokens or 0
                ans.output_tokens += u.output_tokens or 0
                ans.cache_read_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
                ans.cache_write_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
            if last is not None:
                ans.stop_reason = last.stop_reason or ""
                ans.text = "\n".join(b.text for b in last.content if b.type == "text").strip()
        except anthropic.APIStatusError as e:
            ans.error = f"API error {e.status_code}: {e.message}"
        except anthropic.APIConnectionError as e:
            ans.error = f"connection error: {e}"
        ans.tool_calls = len(trace)
        ans.seconds = round(time.time() - started, 1)
        ans.parsed = parse_final(ans.text) if ans.text else {"answer": "", "numbers": [], "store_ids": [], "could_not_answer": False, "unparsed": True}
        return ans
