-- Fluxo profissional de entregas: início de descarregamento, localização pontual,
-- conclusão offline com horário do aparelho e histórico de estornos.

BEGIN;

CREATE TABLE IF NOT EXISTS app.delivery_starts (
    id varchar(90) PRIMARY KEY,
    note_id bigint NOT NULL REFERENCES app.notes(id),
    driver_id bigint NOT NULL REFERENCES app.drivers(id),
    started_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    latitude numeric(9,6),
    longitude numeric(9,6),
    accuracy_m numeric(8,1),
    CHECK ((latitude IS NULL AND longitude IS NULL AND accuracy_m IS NULL) OR
           (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 AND
            accuracy_m BETWEEN 0 AND 10000))
);

ALTER TABLE app.delivery_events
    ADD COLUMN IF NOT EXISTS start_id varchar(90) REFERENCES app.delivery_starts(id),
    ADD COLUMN IF NOT EXISTS completed_at timestamptz,
    ADD COLUMN IF NOT EXISTS occurrence_type varchar(30) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS observation varchar(1000) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS expected_quantity varchar(40) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS received_quantity varchar(40) NOT NULL DEFAULT '';

UPDATE app.delivery_events SET completed_at=created_at WHERE completed_at IS NULL;
ALTER TABLE app.delivery_events
    ALTER COLUMN completed_at SET DEFAULT now(),
    ALTER COLUMN completed_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS delivery_starts_by_note
    ON app.delivery_starts(note_id, recorded_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS delivery_events_start_once
    ON app.delivery_events(start_id) WHERE start_id IS NOT NULL;

ALTER TABLE app.delivery_starts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE app.delivery_starts FROM PUBLIC, anon, authenticated;

COMMIT;
