import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel
from google.cloud import pubsub_v1

app = FastAPI()

# Config via environment variable, never hardcoded (injected by Cloud Run
# from deploy.sh).
PROJECT_ID = os.environ["GCP_PROJECT"]
TOPIC_ID = os.environ["PUBSUB_TOPIC"]

publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)


class RecordIn(BaseModel):
    type: str
    value: float
    unit: str


@app.post("/records", status_code=202)
def create_record(payload: RecordIn):
    record_id = str(uuid.uuid4())
    message = {
        "id": record_id,
        "type": payload.type,
        "value": payload.value,
        "unit": payload.unit,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }

    # .result() waits for Pub/Sub's ack before responding to the client:
    # if Pub/Sub doesn't confirm it received the message, we'd rather the
    # POST fail than have the client think something was saved that was lost.
    future = publisher.publish(topic_path, json.dumps(message).encode("utf-8"))
    future.result(timeout=10)

    print(f"Published record {record_id} to topic {TOPIC_ID}")

    # 202, not 201: the resource doesn't exist yet, we've only accepted it.
    return {"id": record_id, "status": "pending"}
