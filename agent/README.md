# Self-correcting coding agent — plan / act / check / iterate

A small agent built directly on the Anthropic API (not Claude Code) that implements `records_core.py` from a written spec, verifying its own work against a real test suite instead of self-reporting success.

## Why an objective check matters

The distinguishing feature here isn't "an LLM writes code" — it's that the harness never trusts the model's own claim of completion. The only success criterion is that `run_tests()`, which runs real `pytest` in a subprocess, reports `PASSED`. If the model claims the task is done but the tests fail, the loop keeps iterating with the real failure output. If it exhausts its iteration budget (`MAX_ITERATIONS = 6`) without converging, it reports that honestly rather than claiming success.

## How the loop maps

| Phase | Implementation |
|---|---|
| **Plan** | The model's reasoning between tool calls, grounded in `spec.md` — an explicit, testable contract rather than an open-ended prompt. |
| **Act** | The `write_file` tool — the model's only way to modify `records_core.py`. |
| **Check** | The `run_tests` tool — runs `pytest` in a subprocess and returns the raw output. Pass/fail is determined by the test runner's exit code, not the model's assessment. |
| **Iterate** | A bounded loop in `run_agent.py` that feeds each round's real test output back to the model, capped at `MAX_ITERATIONS`. |

## Task

`spec.md` describes a small, self-contained module: payload validation plus an idempotent key-value store, mirroring the redelivery-safe write pattern used in [`../processor-service/main.py`](../processor-service/main.py) (`ON CONFLICT (id) DO NOTHING`) — isolated here as a pure, cloud-free module so the agent loop runs in seconds without needing a live database.

## Running it

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python run_agent.py
```

Output shows each iteration: which tool the model calls, what the harness returns, and whether `run_tests` reports `PASSED`. `records_core.py` starts empty on purpose, so the first test run fails and the loop has real work to do.

Typical cost: a few cents of API usage for the full run.
