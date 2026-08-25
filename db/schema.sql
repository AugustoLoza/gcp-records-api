-- The row's existence IMPLIES "processed": there's no status column
-- because a "pending" row is never inserted (ingest-service never touches the DB).
CREATE TABLE IF NOT EXISTS records (
    id UUID PRIMARY KEY,
    type TEXT NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,   -- when ingest-service published the event
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()  -- when the processor persisted it
);
