-- Perfis administrativos com migração segura das contas existentes.
-- Contas anteriores à migração eram administradores completos e preservam esse acesso.

BEGIN;

ALTER TABLE app.admin_users
    ADD COLUMN IF NOT EXISTS role varchar(20);

UPDATE app.admin_users SET role='admin' WHERE role IS NULL;

ALTER TABLE app.admin_users
    ALTER COLUMN role SET DEFAULT 'operator',
    ALTER COLUMN role SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='admin_users_role_check'
          AND conrelid='app.admin_users'::regclass
    ) THEN
        ALTER TABLE app.admin_users
            ADD CONSTRAINT admin_users_role_check
            CHECK (role IN ('admin','operator','auditor'));
    END IF;
END $$;

ALTER TABLE app.admin_users ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON app.admin_users FROM PUBLIC, anon, authenticated;

COMMIT;
