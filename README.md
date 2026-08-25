# Records API — GCP, event-driven

API de ingesta de "resultados de salud", con procesamiento asíncrono real
(Pub/Sub) sobre Postgres — pensada como práctica dirigida al stack de
Function Health (Python en GCP, arquitecturas event-driven, Postgres).

## Arquitectura

```
Cliente
  |  POST /records
  v
ingest-service (Cloud Run) --publica evento--> Pub/Sub (topic: records-ingested)
                                                       |
                                                       | push (con token OIDC)
                                                       v
                                              processor-service (Cloud Run)
                                                       |
                                                       v
                                                   Postgres (Cloud SQL)
                                                       ^
                                                       |
                                              GET /records/{id}  (mismo servicio)
```

## Decisiones y por qué (el corazón del challenge)

- **ingest-service no toca la base de datos.** Solo valida el payload,
  genera un `id` (UUID) y publica un evento en Pub/Sub. Responde
  `202 Accepted`, no `201 Created`, porque el recurso todavía no existe —
  solo aceptamos procesarlo. Esta separación es a propósito: es el patrón
  de "clear service boundaries" que pide el rol. Si mañana el volumen de
  ingesta se dispara, escalás el ingest sin tocar el processor, y viceversa.

- **Consistencia eventual, real.** Un `GET /records/{id}` justo después del
  `POST` puede devolver 404 durante un instante, hasta que el processor lo
  persista. No es un bug — es el trade-off explícito de desacoplar ingesta
  de procesamiento. Un cliente bien diseñado hace polling con backoff, no
  asume que el dato está disponible al instante.

- **Idempotencia contra reentregas.** Pub/Sub garantiza *at-least-once
  delivery*: el mismo mensaje puede llegar más de una vez. El INSERT usa
  `ON CONFLICT (id) DO NOTHING` — reprocesar el mismo evento no rompe ni
  duplica. Esto es "diseñar para reliability at scale", no un detalle de SQL.

- **Postgres, no NoSQL.** A diferencia de la versión AWS (DynamoDB), acá el
  esquema es fijo y relacional (`db/schema.sql`), con tipos (`NUMERIC` para
  `value`, `TIMESTAMPTZ` para las fechas) — practicando justo lo que pide el
  puesto: "relational modeling, query performance, data integrity".

- **Autenticación servicio-a-servicio con OIDC, no un secreto compartido.**
  `processor-service` verifica el JWT que Pub/Sub adjunta a cada push
  (`id_token.verify_oauth2_token`), validando que venga realmente de la
  suscripción de Pub/Sub y no de cualquiera que le pegue a `/pubsub/push`.

- **Secrets en Secret Manager, no como env var plana.** La password de
  Postgres se inyecta con `--set-secrets` (Secret Manager), no
  `--set-env-vars`. Diferencia real: un env var normal se ve en la config
  del servicio (`gcloud run services describe`) para cualquiera con permiso
  de lectura; un secret de Secret Manager tiene su propio control de acceso
  y queda auditado por separado.

- **`gcloud run deploy --source`, sin Docker local.** Esta máquina no tiene
  Docker instalado — `--source` sube el código y lo builda con Cloud Build
  en la nube. Útil para arrancar rápido; en un equipo real normalmente se
  builda en CI (Cloud Build/GitHub Actions) de todos modos.

## ⚠️ Costo: distinto a la versión AWS

DynamoDB (versión AWS) tiene free tier permanente. **Cloud SQL no** — la
instancia más chica (`db-f1-micro`) cuesta unos pocos dólares por día si
queda corriendo. Para no gastar de más:

```bash
# Pausarla cuando no la estés usando:
gcloud sql instances patch records-db --activation-policy=NEVER
# Prenderla de nuevo:
gcloud sql instances patch records-db --activation-policy=ALWAYS
# Borrar todo al terminar el challenge:
gcloud sql instances delete records-db
```

## Estructura

```
db/schema.sql            # DDL de la tabla records
ingest-service/
  main.py                 # POST /records -> publica en Pub/Sub
  requirements.txt
  Dockerfile
processor-service/
  main.py                 # push handler de Pub/Sub + GET /records/{id}
  db.py                    # conexión a Cloud SQL vía el connector oficial
  requirements.txt
  Dockerfile
deploy.sh                 # gcloud paso a paso (APIs, Cloud SQL, Pub/Sub, Cloud Run, IAM)
```

## Cómo mapea a los requisitos del puesto

| Requisito del job | Dónde está acá |
|---|---|
| Python backend | `ingest-service`, `processor-service` (FastAPI) |
| GCP: Pub/Sub, Cloud Run | Todo el stack |
| Event-driven, colas, procesamiento async | Pub/Sub topic + push subscription entre los dos servicios |
| Boundaries claros entre servicios | ingest nunca toca Postgres; processor nunca publica eventos |
| Postgres, modelado relacional, integridad | `db/schema.sql`, `ON CONFLICT`, tipos explícitos |
| Reliability a escala | Idempotencia, verificación de token, `202` vs `201` a propósito |
| Ingesta → procesamiento → mostrar al usuario | Es literalmente el flujo `POST` → Pub/Sub → processor → `GET` |

Lo que falta para un espejo 100% completo (no crítico para el challenge):
GraphQL (piden Postgres+GraphQL como bonus), Datadog real (acá son
`print()` que van a Cloud Logging — el equivalente conceptual, pero no
Datadog en sí), y tests automatizados.

## Prerrequisitos

1. Cuenta de GCP con billing habilitado (crédito gratis de USD 300 por 90
   días para cuentas nuevas — igual, ver el aviso de costo de Cloud SQL
   arriba, ese crédito se agota).
2. [gcloud CLI](https://cloud.google.com/sdk/docs/install) instalado, y
   `gcloud auth login` corrido (abre el navegador, vos iniciás sesión, no yo).
3. Un proyecto de GCP creado (`gcloud projects create tu-proyecto-id`).

## Deploy

```bash
export PROJECT_ID=tu-proyecto-id
bash deploy.sh
```

## Probar

```bash
curl -X POST $INGEST_URL/records \
  -H "Content-Type: application/json" \
  -d '{"type":"blood_test","value":120,"unit":"mg/dL"}'
# -> {"id": "...", "status": "pending"}

curl $PROCESSOR_URL/records/<id>
# puede dar 404 si todavia no proceso el evento; reintentar en 1-2s
```

## Troubleshooting real que nos encontramos

**`POST /pubsub/push` devuelve 401 aunque todo el IAM esté bien configurado.**
Los logs mostraban que el push de Pub/Sub llegaba (confirmando topic,
suscripción y el binding de `roles/run.invoker` correctos) pero
`processor-service` lo rechazaba. Causa: por default, el `audience` del token
OIDC que genera Pub/Sub es la **URL completa del push endpoint**
(`.../pubsub/push`), no el origen del servicio. Nuestro código valida el
token contra `SERVICE_URL` (sin el path) — mismatch, siempre 401. Se arregla
seteando `--push-auth-token-audience` explícitamente en la suscripción (ya
corregido en `deploy.sh`). Para diagnosticarlo sin adivinar: generamos un
token real impersonando la cuenta de servicio de push
(`gcloud auth print-identity-token --impersonate-service-account=... --audiences=...`)
y lo mandamos a mano al endpoint — así separamos "¿bug en mi verificación?"
de "¿bug en la config de Pub/Sub?" en vez de cambiar cosas a ciegas.

**Cloud Run no puede leer el secret de Secret Manager al crear la revisión.**
`Permission denied on secret ... for Revision service account
PROJECT_NUMBER-compute@developer.gserviceaccount.com`. La cuenta de servicio
por defecto de Cloud Run no tiene acceso a Secret Manager por default — hay
que otorgarle `roles/secretmanager.secretAccessor` sobre el secret
explícitamente (ya corregido en `deploy.sh`).

## Logs

```bash
gcloud run services logs read ingest-service --region=$REGION
gcloud run services logs read processor-service --region=$REGION
```
