#!/usr/bin/env bash
# Deploy completo del stack a GCP. Pensado para correr por bloques la primera
# vez (revisá la salida de cada paso), no como un script "y listo".
set -euo pipefail

: "${PROJECT_ID:?Seteá PROJECT_ID primero: export PROJECT_ID=tu-proyecto-gcp}"
REGION="${REGION:-us-central1}"
INSTANCE_NAME="${INSTANCE_NAME:-records-db}"
DB_NAME="${DB_NAME:-records}"
DB_USER="${DB_USER:-records_app}"
TOPIC_ID="${TOPIC_ID:-records-ingested}"
SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-records-ingested-push}"

gcloud config set project "$PROJECT_ID"

echo "== 1/8: habilitando APIs =="
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com

echo "== 2/8: Cloud SQL Postgres (tarda varios minutos la primera vez) =="
# OJO CON EL COSTO: a diferencia de DynamoDB, Cloud SQL NO tiene free tier
# permanente. db-f1-micro son centavos/dolares por dia, no gratis. Cuando
# termines de probar: gcloud sql instances patch "$INSTANCE_NAME" --activation-policy=NEVER
# o directamente borrala con gcloud sql instances delete.
if ! gcloud sql instances describe "$INSTANCE_NAME" >/dev/null 2>&1; then
  gcloud sql instances create "$INSTANCE_NAME" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="$REGION"
    # Con IP publica (default) a proposito: el Cloud SQL Python Connector
    # autentica via IAM + mTLS sin depender de "authorized networks", y asi
    # evitamos necesitar un VPC Access Connector solo para este challenge.
fi

DB_PASSWORD="$(openssl rand -base64 24)"
gcloud sql users create "$DB_USER" --instance="$INSTANCE_NAME" --password="$DB_PASSWORD" 2>/dev/null || true
gcloud sql databases create "$DB_NAME" --instance="$INSTANCE_NAME" 2>/dev/null || true

echo "== 3/8: password en Secret Manager (nunca en texto plano en Cloud Run) =="
printf '%s' "$DB_PASSWORD" | gcloud secrets create db-password --data-file=- 2>/dev/null \
  || printf '%s' "$DB_PASSWORD" | gcloud secrets versions add db-password --data-file=-

# La cuenta de servicio por defecto de Cloud Run (PROJECT_NUMBER-compute@...) no
# tiene acceso a los secrets por default — hay que otorgárselo explícitamente,
# si no el deploy falla al crear la revisión con "Permission denied on secret".
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud secrets add-iam-policy-binding db-password \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" 2>/dev/null || true

INSTANCE_CONNECTION_NAME="$(gcloud sql instances describe "$INSTANCE_NAME" --format='value(connectionName)')"

echo "== 4/8: cargá db/schema.sql =="
echo "Paso manual (una sola vez): abrí Cloud SQL Studio en la consola de GCP"
echo "para la instancia '$INSTANCE_NAME', conectate a la DB '$DB_NAME' y"
echo "pegá el contenido de db/schema.sql. Segui cuando lo hayas hecho."
read -rp "Presioná Enter cuando el schema ya este creado... "

echo "== 5/8: Pub/Sub topic =="
gcloud pubsub topics create "$TOPIC_ID" 2>/dev/null || true

echo "== 6/8: deploy de processor-service =="
# --source builda con Cloud Build (no hace falta Docker instalado localmente)
gcloud run deploy processor-service \
  --source=./processor-service \
  --region="$REGION" \
  --allow-unauthenticated \
  --add-cloudsql-instances="$INSTANCE_CONNECTION_NAME" \
  --set-env-vars="INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME,DB_USER=$DB_USER,DB_NAME=$DB_NAME,SERVICE_URL=placeholder" \
  --set-secrets="DB_PASSWORD=db-password:latest"

PROCESSOR_URL="$(gcloud run services describe processor-service --region="$REGION" --format='value(status.url)')"

# Redeploy de config solo para fijar SERVICE_URL: no se conoce la URL real
# hasta que el servicio existe (se usa como "audience" del token OIDC).
gcloud run services update processor-service \
  --region="$REGION" \
  --set-env-vars="INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME,DB_USER=$DB_USER,DB_NAME=$DB_NAME,SERVICE_URL=$PROCESSOR_URL"

echo "== 7/8: identidad para que Pub/Sub llame al processor con OIDC =="
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
  # OJO: sin --push-auth-token-audience, el audience del token OIDC que genera
  # Pub/Sub por default es la URL COMPLETA del push-endpoint (con /pubsub/push
  # incluido), no el origen del servicio. Como processor-service/main.py valida
  # el audience contra SERVICE_URL (sin el path), sin este flag todo push
  # llega con 401 aunque el resto de la config esté bien.

echo "== 8/8: deploy de ingest-service =="
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
echo "Listo."
echo "Ingest URL: $INGEST_URL"
echo "Probá con:"
echo "curl -X POST $INGEST_URL/records -H 'Content-Type: application/json' -d '{\"type\":\"blood_test\",\"value\":120,\"unit\":\"mg/dL\"}'"
