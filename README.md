# Records API — GCP, event-driven

Health "results" ingestion API with real asynchronous processing (Pub/Sub)
on top of Postgres — built as focused practice for a Python-on-GCP,
event-driven-architecture, Postgres stack.

## Architecture

```
Client
  |  POST /records
  v
ingest-service (Cloud Run) --publishes event--> Pub/Sub (topic: records-ingested)
                                                       |
                                                       | push (OIDC token)
                                                       v
                                              processor-service (Cloud Run)
                                                       |
                                                       v
                                                   Postgres (Cloud SQL)
                                                       ^
                                                       |
                                              GET /records/{id}  (same service)
```

## Decisions and why (the heart of the project)

- **ingest-service never touches the database.** It only validates the
  payload, generates an `id` (UUID), and publishes an event to Pub/Sub. It
  responds `202 Accepted`, not `201 Created`, because the resource doesn't
  exist yet — we've only accepted the request to process it. This separation
  is deliberate: it's the "clear service boundaries" pattern. If ingestion
  volume spikes tomorrow, you scale ingest without touching the processor,
  and vice versa.

- **Real eventual consistency.** A `GET /records/{id}` right after the
  `POST` can return 404 for a brief moment, until the processor persists it.
  That's not a bug — it's the explicit trade-off of decoupling ingestion
  from processing. A well-designed client polls with backoff instead of
  assuming the data is available instantly.

- **Idempotency against redelivery.** Pub/Sub guarantees *at-least-once
  delivery*: the same message can arrive more than once. The INSERT uses
  `ON CONFLICT (id) DO NOTHING` — reprocessing the same event neither breaks
  nor duplicates anything. This is "designing for reliability at scale," not
  a SQL detail.

- **Postgres, not NoSQL.** Unlike the AWS version (DynamoDB), here the
  schema is fixed and relational (`db/schema.sql`), with real types
  (`NUMERIC` for `value`, `TIMESTAMPTZ` for dates) — practicing exactly what
  the role asks for: "relational modeling, query performance, data
  integrity."

- **Service-to-service auth via OIDC, not a shared secret.**
  `processor-service` verifies the JWT that Pub/Sub attaches to every push
  (`id_token.verify_oauth2_token`), confirming it really came from the
  Pub/Sub subscription and not from anyone hitting `/pubsub/push` directly.

- **Secrets in Secret Manager, not a plain env var.** The Postgres password
  is injected via `--set-secrets` (Secret Manager), not `--set-env-vars`.
  Real difference: a plain env var is visible in the service config
  (`gcloud run services describe`) to anyone with read access; a Secret
  Manager secret has its own access control and is audited separately.

- **`gcloud run deploy --source`, no local Docker.** This machine doesn't
  have Docker installed — `--source` uploads the code and builds it with
  Cloud Build in the cloud. Useful for getting started quickly; a real team
  would typically build in CI (Cloud Build/GitHub Actions) anyway.

## ⚠️ Cost: different from the AWS version

DynamoDB (AWS version) has a permanent free tier. **Cloud SQL does not** —
the smallest instance (`db-f1-micro`) costs a few dollars per day if left
running. To avoid overspending:

```bash
# Pause it when you're not using it:
gcloud sql instances patch records-db --activation-policy=NEVER
# Turn it back on:
gcloud sql instances patch records-db --activation-policy=ALWAYS
# Delete everything once you're done:
gcloud sql instances delete records-db
```

## Structure

```
db/schema.sql            # DDL for the records table
ingest-service/
  main.py                 # POST /records -> publishes to Pub/Sub
  requirements.txt
  Dockerfile
processor-service/
  main.py                 # Pub/Sub push handler + GET /records/{id}
  db.py                    # Cloud SQL connection via the official connector
  requirements.txt
  Dockerfile
deploy.sh                 # step-by-step gcloud (APIs, Cloud SQL, Pub/Sub, Cloud Run, IAM)
```

## How this maps to typical job requirements

| Requirement | Where it lives here |
|---|---|
| Python backend | `ingest-service`, `processor-service` (FastAPI) |
| GCP: Pub/Sub, Cloud Run | The whole stack |
| Event-driven, queues, async processing | Pub/Sub topic + push subscription between the two services |
| Clear boundaries between services | ingest never touches Postgres; processor never publishes events |
| Postgres, relational modeling, integrity | `db/schema.sql`, `ON CONFLICT`, explicit types |
| Reliability at scale | Idempotency, token verification, `202` vs `201` used deliberately |
| Ingest → process → serve | This is literally the `POST` → Pub/Sub → processor → `GET` flow |

Not covered here (not critical for the exercise): GraphQL, real Datadog
integration (logs here are `print()` statements going to Cloud Logging — the
conceptual equivalent, but not Datadog itself), and automated tests for the
deployed services.

## Prerequisites

1. A GCP account with billing enabled (new accounts get $300 in free credit
   for 90 days — still, see the Cloud SQL cost note above, that credit runs
   out).
2. [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed, with
   `gcloud auth login` already run (opens the browser, you sign in, not me).
3. A GCP project created (`gcloud projects create your-project-id`).

## Deploy

```bash
export PROJECT_ID=your-project-id
bash deploy.sh
```

## Test it

```bash
curl -X POST $INGEST_URL/records \
  -H "Content-Type: application/json" \
  -d '{"type":"blood_test","value":120,"unit":"mg/dL"}'
# -> {"id": "...", "status": "pending"}

curl $PROCESSOR_URL/records/<id>
# may return 404 if the event hasn't been processed yet; retry in 1-2s
```

## Real troubleshooting we ran into

**`POST /pubsub/push` returns 401 even though IAM looks correctly
configured.** Logs showed the Pub/Sub push was arriving (confirming the
topic, subscription, and `roles/run.invoker` binding were all correct), but
`processor-service` was rejecting it. Root cause: by default, the `audience`
claim on the OIDC token Pub/Sub generates is the **full push endpoint URL**
(`.../pubsub/push`), not the service's origin. Our code validates the token
against `SERVICE_URL` (no path) — mismatch, always 401. Fixed by explicitly
setting `--push-auth-token-audience` on the subscription (already fixed in
`deploy.sh`). To diagnose it without guessing: we minted a real token by
impersonating the push service account
(`gcloud auth print-identity-token --impersonate-service-account=... --audiences=...`)
and sent it to the endpoint by hand — separating "is my verification code
wrong?" from "is the Pub/Sub config wrong?" instead of changing things
blindly.

**Cloud Run can't read the Secret Manager secret when creating the
revision.** `Permission denied on secret ... for Revision service account
PROJECT_NUMBER-compute@developer.gserviceaccount.com`. Cloud Run's default
service account has no Secret Manager access by default — it needs
`roles/secretmanager.secretAccessor` on the secret granted explicitly
(already fixed in `deploy.sh`).

## Logs

```bash
gcloud run services logs read ingest-service --region=$REGION
gcloud run services logs read processor-service --region=$REGION
```
