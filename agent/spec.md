# records_core — spec

Implement `records_core.py` with exactly these symbols.

## `class ValidationError(Exception)`
Raised by `validate_payload` when the input is invalid.

## `class Record`
A dataclass (or equivalent) with fields: `id: str`, `type: str`, `value: float`, `unit: str`.
Two Records are equal if all four fields are equal.

## `def validate_payload(data: dict) -> Record`
1. `type`, `value`, and `unit` are all required. Missing any of them raises `ValidationError`.
2. `type` and `unit` must be non-empty strings.
3. `value` must be a number (int or float) — not a string, even a numeric-looking one.
4. On success, generates a new random unique `id` (e.g. `uuid.uuid4()`) — never accept an id from the caller.
5. Each call generates a *different* id, even for identical input.

## `Storage` interface
- `get(self, id: str) -> Record | None`
- `upsert(self, record: Record) -> bool` — returns `True` if this `record.id` was newly
  inserted, `False` if a record with that id already existed. On a duplicate call the
  stored record is NOT overwritten. This mirrors real Pub/Sub at-least-once delivery,
  where the same message can be redelivered — see `../processor-service/main.py`'s
  `ON CONFLICT (id) DO NOTHING` for the production equivalent of this same rule.
  A formal `typing.Protocol` is not required to pass the tests.

## `class InMemoryStorage`
Implements the `Storage` interface using a plain dict. No persistence, no I/O — this
exists so the logic is testable without a real database (the real implementation talks
to Cloud SQL; this one is the fast, cloud-free version of the same contract).

---

Run `pytest test_records_core.py -v` to check your work. Do not consider the task done
until every test passes — the test output is the ground truth, not your own assessment
of the code.
