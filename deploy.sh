#!/usr/bin/env bash
# Full stack deploy to GCP. Meant to be run block by block the first time
# (review each step's output), not as a "run it and forget it" script.
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID first: export PROJECT_ID=your-gcp-project}"
REGION="${REGION:-us-central1}"
INSTANCE_NAME="${INSTANCE_NAME:-records-db}"
DB_NAME="${DB_NAME:-records}"
DB_USER="${DB_USER:-records_app}"
TOPIC_ID="${TOPIC_ID:-records-ingested}"
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-records-ingested-push}"

gcloud config set project "$PROJECT_ID"

echo "== 1/8: enabling APIs =="
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com

echo "== 2/8: Cloud SQL Postgres (takes a few minutes the first time) =="
# COST WARNING: unlike DynamoDB, Cloud SQL has NO permanent free tier.
# db-f1-micro runs a few cents/dollars per day, not free. When you're done
# testing: gcloud sql instances patch "$INSTANCE_NAME" --activation-policy=NEVER
# or delete it outright with gcloud sql instances delete.
if ! gcloud sql instances describe "$INSTANCE_NAME" >/dev/null 2>&1; then
  gcloud sql instances create "$INSTANCE_NAME" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="$REGION"
    # Public IP (default) on purpose: the Cloud SQL Python Connector
    # authenticates via IAM + mTLS without depending on "authorized
    # networks", which avoids needing a VPC Access Connector just for
    # this project.
fi

DB_PASSWORD="$(openssl rand -base64 24)"
gcloud sql users create "$DB_USER" --instance="$INSTANCE_NAME" --password="$DB_PASSWORD" 2>/dev/null || true
gcloud sql databases create "$DB_NAME" --instance="$INSTANCE_NAME" 2>/dev/null || true

echo "== 3/8: password in Secret Manager (never plaintext in Cloud Run) =="
printf '%s' "$DB_PASSWORD" | gcloud secrets create db-password --data-file=- 2>/dev/null \
  || printf '%s' "$DB_PASSWORD" | gcloud secrets versions add db-password --data-file=-

# Cloud Run's default service account (PROJECT_NUMBER-compute@...) has no
# access to secrets by default — it needs to be granted explicitly, or the
# deploy fails when creating the revision with "Permission denied on secret".
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud secrets add-iam-policy-binding db-password \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" 2>/dev/null || true

INSTANCE_CONNECTION_NAME="$(gcloud sql instances describe "$INSTANCE_NAME" --format='value(connectionName)')"

echo "== 4/8: load db/schema.sql =="
echo "Manual step (one time only): open Cloud SQL Studio in the GCP console"
echo "for instance '$INSTANCE_NAME', connect to database '$DB_NAME', and"
echo "paste the contents of db/schema.sql. Continue once you've done that."
read -rp "Press Enter once the schema has been created... "

echo "== 5/8: Pub/Sub topic =="
gcloud pubsub topics create "$TOPIC_ID" 2>/dev/null || true

echo "== 6/8: deploy processor-service =="
# --source builds with Cloud Build (no need for Docker installed locally)
gcloud run deploy processor-service \
  --source=./processor-service \
  --region="$REGION" \
  --allow-unauthenticated \
  --add-cloudsql-instances="$INSTANCE_CONNECTION_NAME" \
  --set-env-vars="INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME,DB_USER=$DB_USER,DB_NAME=$DB_NAME,SERVICE_URL=placeholder" \
  --set-secrets="DB_PASSWORD=db-password:latest"

PROCESSOR_URL="$(gcloud run services describe processor-service --region="$REGION" --format='value(status.url)')"

# Redeploy the config just to set the real SERVICE_URL: the URL isn't known
# until the service exists (it's used as the OIDC token's "audience").
gcloud run services update processor-service \
  --region="$REGION" \
  --set-env-vars="INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME,DB_USER=$DB_USER,DB_NAME=$DB_NAME,SERVICE_URL=$PROCESSOR_URL"

echo "== 7/8: identity for Pub/Sub to call the processor via OIDC =="
PUSH_SA="pubsub-push-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts create pubsub-push-invoker \
  --display-name="Pub/Sub push invoker" 2>/dev/null || true

gcloud run services add-iam-policy-binding processor-service \
  --region="$REGION" \
  --member="serviceAccount:${PUSH_SA}" \
  --role="roles/run.invoker"

gcloud pubsub subscriptions create "$SUBSCRIPTION_ID" \
  --topic="$TOPIC_ID" \
  --push-endpoint="${PROCESSOR_URL}/pubsub/push" \
  --push-auth-service-account="$PUSH_SA" \
  --push-auth-token-audience="$PROCESSOR_URL" \
  --ack-deadline=30 2>/dev/null || true
  # NOTE: without --push-auth-token-audience, the audience claim on the OIDC
  # token Pub/Sub generates defaults to the FULL push-endpoint URL
  # (including /pubsub/push), not the service's origin. Since
  # processor-service/main.py validates the audience against SERVICE_URL
  # (no path), every push returns 401 without this flag even though the
  # rest of the config is correct.

echo "== 8/8: deploy ingest-service =="
gcloud run deploy ingest-service \
  --source=./ingest-service \
  --region="$REGION" \
  --allow-unauthenticated \
  --set-env-vars="GCP_PROJECT=$PROJECT_ID,PUBSUB_TOPIC=$TOPIC_ID"

INGEST_SA="$(gcloud run services describe ingest-service --region="$REGION" --format='value(spec.template.spec.serviceAccountName)')"
gcloud pubsub topics add-iam-policy-binding "$TOPIC_ID" \
  --member="serviceAccount:${INGEST_SA}" \
  --role="roles/pubsub.publisher"

INGEST_URL="$(gcloud run services describe ingest-service --region="$REGION" --format='value(status.url)')"
echo ""
echo "Done."
echo "Ingest URL: $INGEST_URL"
echo "Try it with:"
echo "curl -X POST $INGEST_URL/records -H 'Content-Type: application/json' -d '{\"type\":\"blood_test\",\"value\":120,\"unit\":\"mg/dL\"}'"
