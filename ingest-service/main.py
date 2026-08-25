import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel
from google.cloud import pubsub_v1

app = FastAPI()

# Config por variable de entorno, nunca hardcodeada (inyectada por Cloud Run
# a partir de deploy.sh).
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

    # .result() espera el ack de Pub/Sub antes de responder al cliente:
    # si Pub/Sub no confirma que recibio el mensaje, preferimos que el
    # POST falle a que el cliente crea que se guardo algo que se perdio.
    future = publisher.publish(topic_path, json.dumps(message).encode("utf-8"))
    future.result(timeout=10)

    print(f"Published record {record_id} to topic {TOPIC_ID}")

    # 202, no 201: todavia no existe el recurso, solo aceptamos procesarlo.
    return {"id": record_id, "status": "pending"}
