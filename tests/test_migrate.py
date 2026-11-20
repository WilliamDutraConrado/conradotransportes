import unittest

from conrado.migrate import apply_migration


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, *, verification=False):
        self.verification = verification
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return Result((True, True, True) if self.verification else None)


class MigrationTests(unittest.TestCase):
    def test_known_migration_is_applied_and_verified(self):
        migration, verification = Connection(), Connection(verification=True)
        connections = iter((migration, verification))

        apply_migration("postgresql://redacted", "003_persistent_login_limits",
                        connect=lambda *_args, **_kwargs: next(connections))

        self.assertIn("CREATE TABLE IF NOT EXISTS app.login_limits", migration.executed[0][0])
        self.assertIn("rowsecurity", verification.executed[0][0])

    def test_admin_migration_verifies_both_private_tables(self):
        migration, verification = Connection(), Connection(verification=True)
        connections = iter((migration, verification))

        apply_migration("postgresql://redacted", "004_admin_auth",
                        connect=lambda *_args, **_kwargs: next(connections))

        self.assertIn("CREATE TABLE IF NOT EXISTS app.admin_users", migration.executed[0][0])
        self.assertEqual([params[0] for _, params in verification.executed],
                         ["admin_users", "admin_sessions"])

    def test_roles_migration_preserves_existing_admins_and_verifies_table(self):
        migration, verification = Connection(), Connection(verification=True)
        connections = iter((migration, verification))

        apply_migration("postgresql://redacted", "005_admin_roles",
                        connect=lambda *_args, **_kwargs: next(connections))

        sql = migration.executed[0][0]
        self.assertIn("UPDATE app.admin_users SET role='admin'", sql)
        self.assertIn("admin_users_role_check", sql)
        self.assertEqual(verification.executed[0][1], ("admin_users",))

    def test_unknown_migration_is_rejected_before_connection(self):
        with self.assertRaisesRegex(RuntimeError, "não autorizada"):
            apply_migration("postgresql://redacted", "../../arquivo")

    def test_retention_migration_adds_indexes_and_verifies_private_tables(self):
        migration, verification = Connection(), Connection(verification=True)
        connections = iter((migration, verification))

        apply_migration("postgresql://redacted", "006_retention_indexes",
                        connect=lambda *_args, **_kwargs: next(connections))

        sql = migration.executed[0][0]
        self.assertIn("audit_by_happened_at", sql)
        self.assertIn("evidence_by_created_at", sql)
        self.assertEqual([params[0] for _, params in verification.executed],
                         ["audit", "evidence", "delivery_events", "admin_sessions", "driver_sessions"])

    def test_delivery_workflow_migration_is_additive_and_private(self):
        migration, verification = Connection(), Connection(verification=True)
        connections = iter((migration, verification))

        apply_migration("postgresql://redacted", "007_delivery_workflow",
                        connect=lambda *_args, **_kwargs: next(connections))

        sql = migration.executed[0][0]
        self.assertIn("CREATE TABLE IF NOT EXISTS app.delivery_starts", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS completed_at", sql)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertEqual([params[0] for _, params in verification.executed],
                         ["delivery_starts", "delivery_events"])

    def test_connection_error_does_not_leak_secret(self):
        def connect(*args, **kwargs):
            raise OSError("credencial-sensivel-simulada")

        with self.assertRaisesRegex(RuntimeError, "sem expor") as context:
            apply_migration("postgresql://redacted", "003_persistent_login_limits", connect=connect)
        self.assertNotIn("credencial-sensivel", str(context.exception))


if __name__ == "__main__":
    unittest.main()
