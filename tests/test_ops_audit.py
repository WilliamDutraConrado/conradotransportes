import unittest

from conrado.ops_audit import audit_integrity


class Cursor:
    def __init__(self, values, paths):
        self.values = iter(values)
        self.paths = paths
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self.last_sql = sql
        return self

    def fetchone(self):
        return (next(self.values),)

    def fetchall(self):
        return [(path,) for path in self.paths]


class Connection:
    def __init__(self, values, paths):
        self.cursor_value = Cursor(values, paths)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self.cursor_value


class Storage:
    def __init__(self, missing=()):
        self.missing = set(missing)

    def exists(self, path):
        return path not in self.missing


class OpsAuditTests(unittest.TestCase):
    def test_integrity_success_reports_only_expired_sessions(self):
        connect = lambda *args, **kwargs: Connection([2, 0, 0], [
            "viagens/3/notas/2/event-123456789-abcdef012345.jpg",
        ])

        result = audit_integrity("postgresql://placeholder", Storage(), connect=connect)

        self.assertEqual(result["expired_sessions"], 2)
        self.assertEqual(result["evidence_checked"], 1)
        self.assertEqual(result["issues"], [])

    def test_integrity_detects_database_and_storage_mismatches(self):
        path = "viagens/3/notas/2/event-123456789-abcdef012345.jpg"
        connect = lambda *args, **kwargs: Connection([0, 1, 1], [path])

        result = audit_integrity("postgresql://placeholder", Storage([path]), connect=connect)

        self.assertEqual(result["missing_objects"], 1)
        self.assertEqual(len(result["issues"]), 3)

    def test_connection_error_is_sanitized(self):
        def connect(*args, **kwargs):
            raise OSError("credencial-sensivel-simulada")

        with self.assertRaisesRegex(RuntimeError, "Falha segura") as context:
            audit_integrity("postgresql://placeholder", Storage(), connect=connect)
        self.assertNotIn("credencial-sensivel", str(context.exception))


if __name__ == "__main__":
    unittest.main()
