# Self-correcting coding agent — plan / act / check / iterate

A small, real agent built with the raw Anthropic API (not Claude Code) that
implements `records_core.py` from `spec.md`, verifying its own work against a
real pytest suite instead of self-reporting success.

## Why this exists

Maps directly to three lines from the job posting:

> A habit of designing for reliability, observability, and correctness at scale.
> You build with AI coding agents every day.
> You design agentic workflows and self-correcting loops — plan, act, check,
> iterate — instead of one-shot prompting, so the work actually reaches "done."

The difference between this and "ask Claude for some code" is the
**objective check**: `run_agent.py` never trusts the model's own claim that
it's done. The only success criterion is that `run_tests()` — which runs
real pytest, in a subprocess — reports `PASSED`. If the model says "done" but
the tests fail, the harness keeps iterating. If it runs out of iteration
budget (`MAX_ITERATIONS = 6`), it reports the failure honestly instead of
faking success.

## How the loop maps

| Phase | Where it lives |
|---|---|
| **Plan** | Claude's reasoning (`thinking: adaptive`) between tool calls, guided by `spec.md` — an explicit, testable contract, not a vague prompt ("do it well"). This is what the job posting calls "context engineering." |
| **Act** | The `write_file` tool — the model's only way to modify `records_core.py`. |
| **Check** | The `run_tests` tool — runs real pytest via `subprocess`, returns the raw output. The model can't hallucinate a "passed" — either the exit code is 0, or it isn't. |
| **Iterate** | The `for` loop in `run_agent.py`: each round sends the real test results back to the model. Bounded by `MAX_ITERATIONS`, with an honest failure report if it never converges. |

## Setup

This machine doesn't have Python installed — needed before running this:

1. Install [Python 3.10+](https://www.python.org/downloads/).
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Get an Anthropic API key from [console.anthropic.com](https://console.anthropic.com)
   (different from your Claude.ai/Claude Code login — this is the pay-per-use
   API) and set it:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

## Run it

```bash
python run_agent.py
```

You'll see, iteration by iteration: which tool Claude calls, what the harness
returns, and when (if) `run_tests` reports `PASSED`. Expected cost: a few
cents of USD — it's a small task with few iterations.

## What to look at in the output (for the interview)

- The **first** `run_tests` run is expected to fail (the file starts empty)
  — that's intentional, so the loop has real work to do.
- If Claude writes an implementation with a bug (e.g. doesn't generate a
  unique id, or isn't idempotent), you'll see the real pytest output showing
  exactly which assertion failed — and the next iteration reacting to that
  specific output, not to you re-explaining it.
- The harness never says "success" unless the string `PASSED` comes from
  pytest itself. That's the key point to explain in an interview: the
  "check" has to be a source of truth independent of the model, not the
  model grading its own work.

## How this connects to the rest of the project

`spec.md` documents the same idempotency rule implemented in
[`../processor-service/main.py`](../processor-service/main.py) against
Pub/Sub redelivery (`ON CONFLICT (id) DO NOTHING`). Here it's isolated in a
pure module, with no GCP dependency, so the agent loop is fast and cheap to
run — the same reason real engineering teams separate business logic from
infrastructure: it becomes testable without spinning up Cloud SQL.
