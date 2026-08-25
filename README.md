# Records API

An event-driven ingestion API for health results (blood tests, vitals, etc.), built on Google Cloud Platform with two independently deployable services connected through Pub/Sub, backed by a managed Postgres database.

```
Client
  |  POST /records
  v
ingest-service (Cloud Run) --publishes event--> Pub/Sub (topic: records-ingested)
                                                       |
                                                       | push (OIDC-signed)
                                                       v
                                              processor-service (Cloud Run)
                                                       |
                                                       v
                                                   Postgres (Cloud SQL)
                                                       ^
                                                       |
                                              GET /records/{id}  (same service)
```

## Overview

A client submits a health result via `POST /records`. Rather than writing to the database synchronously in the same request, the ingest service publishes an event and returns immediately — the write happens asynchronously in a separate service, which also serves reads once the record exists. This is the ingest → process → serve pattern used for pipelines where a data point needs to be accepted reliably before it's fully processed.

**Stack:** Python (FastAPI) · Google Cloud Run · Pub/Sub · Cloud SQL (Postgres) · Secret Manager · IAM

## API

**`POST /records`**
```json
{ "type": "blood_test", "value": 120, "unit": "mg/dL" }
```
→ `202 Accepted`
```json
{ "id": "e3b0c442-...", "status": "pending" }
```
The record doesn't exist yet at response time — only the request to process it has been accepted.

**`GET /records/{id}`**
→ `200 OK` once processed:
```json
{
  "id": "e3b0c442-...",
  "type": "blood_test",
  "value": 120.0,
  "unit": "mg/dL",
  "ingested_at": "2026-08-19T03:16:51.362673+00:00",
  "processed_at": "2026-08-19T03:16:51.926293+00:00"
}
```
→ `404` if the event hasn't been processed yet (see [Eventual consistency](#eventual-consistency) below).

## Design decisions

**Services split by responsibility, not by convenience.** `ingest-service` validates input, generates an id, and publishes to Pub/Sub — it never opens a database connection, at the code level or the IAM level. `processor-service` is the only thing that writes to or reads from Postgres. Each runs as its own Cloud Run service with its own service account, scoped to only the permission it needs (publish-only for ingest, Cloud SQL + one secret for the processor). Either can be scaled, redeployed, or taken down without the other noticing.

**Eventual consistency, handled deliberately.** Because persistence is decoupled from ingestion, a `GET` immediately after a `POST` can briefly return 404 before the processor catches up — typically under a second, longer on a cold start. This is surfaced explicitly (`202 Accepted`, not `201 Created`) rather than papered over, and a client is expected to poll with backoff rather than assume synchronous availability.

**Idempotent writes.** Pub/Sub guarantees *at-least-once* delivery, so the same event can be redelivered. The insert uses `ON CONFLICT (id) DO NOTHING`, making redelivery a safe no-op instead of a duplicate row or a crash.

**Relational schema, explicit types.** Postgres over a document store, with `NUMERIC` for measurement values and `TIMESTAMPTZ` for timestamps — a fixed, typed schema rather than an implicit one.

**Service-to-service auth via OIDC, not a shared secret.** `processor-service` verifies the identity token Pub/Sub attaches to every push request (`google.oauth2.id_token.verify_oauth2_token`), confirming each request actually originates from the subscription rather than trusting anything that hits the endpoint.

**Secrets via Secret Manager, not plain environment variables.** The database password is injected with `--set-secrets`, which carries its own access control and audit trail — distinct from a plain `--set-env-vars` value, which is visible to anyone with read access to the service configuration.

## Engineering challenges

**OIDC audience mismatch caused every Pub/Sub push to fail with 401**, despite correct IAM bindings. By default, the `audience` claim on the token Pub/Sub generates is the full push endpoint URL (including the path), not the service's origin — which is what the verification code checked against. Diagnosed by minting a real identity token via service account impersonation and calling the endpoint directly, isolating "bug in the verification code" from "bug in the Pub/Sub subscription config" before changing anything. Fixed with an explicit `--push-auth-token-audience` on the subscription.

**Cloud Run couldn't read its own secret on deploy.** The default Compute Engine service account has no Secret Manager access by default; granting a secret reference in the deploy command isn't the same as granting the runtime identity permission to read it. Fixed with an explicit `secretAccessor` IAM binding.

## Cost note

Cloud Run, Pub/Sub, and Secret Manager all scale to zero and cost nothing while idle. Cloud SQL does not — it behaves like an always-on instance and bills hourly regardless of traffic. Pause it when not in active use:

```bash
gcloud sql instances patch records-db --activation-policy=NEVER   # pause
gcloud sql instances patch records-db --activation-policy=ALWAYS  # resume
gcloud sql instances delete records-db                            # tear down
```

## Project structure

```
db/schema.sql            # records table DDL
ingest-service/
  main.py                 # POST /records -> publishes to Pub/Sub
  Dockerfile
processor-service/
  main.py                 # Pub/Sub push handler + GET /records/{id}
  db.py                    # Cloud SQL connection via the official connector
  Dockerfile
deploy.sh                 # end-to-end gcloud deploy (APIs, Cloud SQL, Pub/Sub, Cloud Run, IAM)
agent/                    # a small self-correcting coding agent — see agent/README.md
```

## Running it

**Prerequisites:** a GCP project with billing enabled, and the [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated (`gcloud auth login`).

```bash
export PROJECT_ID=your-project-id
bash deploy.sh
```

`deploy.sh` walks through enabling APIs, provisioning Cloud SQL, storing the database password in Secret Manager, creating the Pub/Sub topic and push subscription, and deploying both Cloud Run services with least-privilege IAM bindings.

## Not included

GraphQL, authentication/authorization on the API itself, structured metrics (logs go to Cloud Logging via `print()`, not a full observability stack), and automated tests for the deployed services. `agent/` has its own test suite and self-correcting verification loop, covered separately in [`agent/README.md`](agent/README.md).
