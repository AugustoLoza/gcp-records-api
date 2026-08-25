import os

from google.cloud.sql.connector import Connector

INSTANCE_CONNECTION_NAME = os.environ["INSTANCE_CONNECTION_NAME"]  # "project:region:instance"
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]  # comes from Secret Manager via --set-secrets, never hardcoded
DB_NAME = os.environ["DB_NAME"]

# Connector reused across requests: opens an mTLS-authenticated tunnel to
# Cloud SQL without needing a public IP on the instance or a proxy sidecar.
_connector = Connector()


def get_connection():
    return _connector.connect(
        INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=DB_USER,
        password=DB_PASSWORD,
        db=DB_NAME,
    )
