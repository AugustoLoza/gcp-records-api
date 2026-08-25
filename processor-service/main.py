import base64
import json
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from db import get_connection

app = FastAPI()

# URL publica de este mismo servicio: es la "audience" esperada en el token
# OIDC que Pub/Sub adjunta a cada push. La setea deploy.sh despues del
# primer deploy (no se conoce la URL hasta que Cloud Run la asigna).
EXPECTED_AUDIENCE = os.environ["SERVICE_URL"]


def verify_pubsub_token(auth_header: str | None) -> None:
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = auth_header.removeprefix("Bearer ")
    try:
        id_token.verify_oauth2_token(token, google_requests.Request(), audience=EXPECTED_AUDIENCE)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Pub/Sub token")


@app.post("/pubsub/push")
async def pubsub_push(request: Request):
    verify_pubsub_token(request.headers.get("Authorization"))

    envelope = await request.json()
    message = envelope.get("message", {})
    raw = base64.b64decode(message.get("data", "")).decode("utf-8")
    data = json.loads(raw)

    conn = get_connection()
    try:
        cur = conn.cursor()
        # ON CONFLICT DO NOTHING: Pub/Sub puede reentregar el mismo mensaje
        # (at-least-once delivery). Sin esto, un reintento normal rompe con
        # un error de primary key duplicada.
        cur.execute(
            """
            INSERT INTO records (id, type, value, unit, ingested_at, processed_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                data["id"],
                data["type"],
                data["value"],
                data["unit"],
                data["ingested_at"],
                datetime.now(timezone.utc),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Processed record {data['id']}")
    return {"status": "ok"}


@app.get("/records/{record_id}")
def get_record(record_id: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, type, value, unit, ingested_at, processed_at FROM records WHERE id = %s",
            (record_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Record not found")

    return {
        "id": str(row[0]),
        "type": row[1],
        "value": float(row[2]),
        "unit": row[3],
        "ingested_at": row[4].isoformat(),
        "processed_at": row[5].isoformat(),
    }
