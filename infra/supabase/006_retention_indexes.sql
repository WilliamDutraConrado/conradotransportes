BEGIN;

-- Índices para diagnóstico e retenção sem alterar nem remover dados.
CREATE INDEX IF NOT EXISTS audit_by_happened_at ON app.audit(happened_at);
CREATE INDEX IF NOT EXISTS evidence_by_created_at ON app.evidence(created_at);
CREATE INDEX IF NOT EXISTS events_by_created_at ON app.delivery_events(created_at);
CREATE INDEX IF NOT EXISTS admin_sessions_by_expiry ON app.admin_sessions(expires_at);
CREATE INDEX IF NOT EXISTS driver_sessions_by_expiry ON app.driver_sessions(expires_at);

-- Reafirma a postura privada das tabelas tocadas pela manutenção.
ALTER TABLE app.audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.delivery_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.admin_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.driver_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON app.audit, app.evidence, app.delivery_events,
    app.admin_sessions, app.driver_sessions FROM PUBLIC, anon, authenticated;

COMMIT;
