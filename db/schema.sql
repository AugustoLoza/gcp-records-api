-- La existencia de la fila IMPLICA "procesado": no hay columna status
-- porque nunca se inserta una fila "pending" (el ingest-service no toca la DB).
CREATE TABLE IF NOT EXISTS records (
    id UUID PRIMARY KEY,
    type TEXT NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,   -- cuando el ingest-service publico el evento
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()  -- cuando el processor lo persistio
);
