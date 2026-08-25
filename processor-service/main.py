import base64
import json
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from db import get_connection

app = FastAPI()

# Public URL of this same service: it's the expected "audience" on the
# OIDC token Pub/Sub attaches to every push. deploy.sh sets it after the
# first deploy (the URL isn't known until Cloud Run assigns it).
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
        # ON CONFLICT DO NOTHING: Pub/Sub can redeliver the same message
        # (at-least-once delivery). Without this, a normal retry would break
        # with a duplicate primary key error.
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
