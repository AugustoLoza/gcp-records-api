import os

from google.cloud.sql.connector import Connector

INSTANCE_CONNECTION_NAME = os.environ["INSTANCE_CONNECTION_NAME"]  # "project:region:instance"
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]  # viene de Secret Manager via --set-secrets, no hardcodeado
DB_NAME = os.environ["DB_NAME"]

# Connector reutilizado entre requests: abre un tunel autenticado con mTLS
# a Cloud SQL sin necesitar IP publica en la instancia ni un proxy sidecar.
_connector = Connector()


def get_connection():
    return _connector.connect(
        INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=DB_USER,
        password=DB_PASSWORD,
        db=DB_NAME,
    )
