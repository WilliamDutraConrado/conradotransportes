-- Limitador de login persistente, sem IP ou placa em texto claro.
-- Aplicar uma única vez em projetos que já receberam 001_schema.sql.

BEGIN;

CREATE TABLE IF NOT EXISTS app.login_limits (
    key_hash char(64) PRIMARY KEY,
    failures smallint NOT NULL DEFAULT 0 CHECK (failures BETWEEN 0 AND 100),
    window_started timestamptz NOT NULL DEFAULT now(),
    blocked_until timestamptz
);

ALTER TABLE app.login_limits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON app.login_limits FROM PUBLIC, anon, authenticated;

COMMIT;
