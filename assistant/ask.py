"""Ask the assistant one question from the command line.

    .venv/bin/python -m assistant.ask "Which stores in Polk County dropped a tier between 2026 Q2 and Q3, and why?"
"""
from __future__ import annotations

import logging
import sys

from assistant.agent import Assistant


def main() -> None:
    logging.disable(logging.WARNING)
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        raise SystemExit('usage: python -m assistant.ask "your question"')
    bot = Assistant()
    try:
        a = bot.ask(question)
    finally:
        bot.close()
    print(a.text or f"(no answer) {a.error}")
    print("\n--- what it ran ---")
    for i, t in enumerate(a.sql_trace, 1):
        status = f"{t.get('rows', '?')} rows" if "rows" in t else t.get("rejected") or t.get("error")
        print(f"{i}. {t['sql'].strip()[:200]}\n   -> {status}")
    print(f"--- {a.tool_calls} tool call(s), {a.seconds}s, {a.input_tokens + a.cache_read_tokens + a.cache_write_tokens} in / "
          f"{a.output_tokens} out tokens, about ${a.cost_usd:.3f}")


if __name__ == "__main__":
    main()
