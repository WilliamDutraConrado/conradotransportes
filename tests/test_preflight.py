import io
import unittest
from unittest.mock import Mock

from conrado.preflight import inspect_database, inspect_storage, TABLES


class FakeCursor:
    def __init__(self, table_names, anon_access=False, authenticated_access=False, without_rls=(),
                 role_column=True, role_constraint=True):
        self.table_names = table_names
        self.anon_access = anon_access
        self.authenticated_access = authenticated_access
        self.without_rls = set(without_rls)
        self.role_column = role_column
        self.role_constraint = role_constraint
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.last_sql = sql

    def fetchall(self):
        if "pg_constraint" in self.last_sql:
            return [("CHECK (role IN ('admin','operator','auditor'))",)] if self.role_constraint else []
        return [(name, name not in self.without_rls) for name in self.table_names]

    def fetchone(self):
        if "information_schema.columns" in self.last_sql:
            return ("role", "NO") if self.role_column else None
        return (self.anon_access, self.authenticated_access)


class FakeConnection:
    def __init__(self, cursor):
        self.c = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self.c


class PreflightTests(unittest.TestCase):
    def test_missing_configuration(self):
        self.assertTrue(inspect_database(""))
        self.assertTrue(inspect_storage("", ""))

    def test_detect_missing_table(self):
        conn = FakeConnection(FakeCursor(["drivers"]))
        result = inspect_database("postgresql://redacted", connect=lambda *_a, **_kw: conn)
        self.assertIn("trips", " ".join(result))

    def test_detect_public_schema_access(self):
        conn = FakeConnection(FakeCursor(TABLES, anon_access=True))
        self.assertIn("anon", " ".join(inspect_database("hidden", connect=lambda *_a, **_kw: conn)))

    def test_detect_authenticated_schema_access_and_missing_rls(self):
        conn = FakeConnection(FakeCursor(TABLES, authenticated_access=True, without_rls=("notes",)))
        result = " ".join(inspect_database("hidden", connect=lambda *_a, **_kw: conn))
        self.assertIn("authenticated", result)
        self.assertIn("notes", result)

    def test_successful_database(self):
        conn = FakeConnection(FakeCursor(TABLES))
        self.assertEqual(inspect_database("hidden", connect=lambda *_a, **_kw: conn), [])

    def test_detect_missing_role_schema(self):
        conn = FakeConnection(FakeCursor(TABLES, role_column=False, role_constraint=False))
        result = " ".join(inspect_database("hidden", connect=lambda *_a, **_kw: conn))
        self.assertIn("perfil administrativo", result)
        self.assertIn("restrição", result)

    def test_private_bucket(self):
        response = io.BytesIO(
            b'{"id":"canhotos","public":false,"file_size_limit":5242880,'
            b'"allowed_mime_types":["image/jpeg","image/png"]}'
        )
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *args: None
        self.assertEqual(inspect_storage("https://example.supabase.co", "secret", opener=lambda *_a, **_kw: response), [])

    def test_public_bucket_rejected(self):
        response = io.BytesIO(
            b'{"id":"canhotos","public":true,"file_size_limit":5242880,'
            b'"allowed_mime_types":["image/jpeg","image/png"]}'
        )
        self.assertTrue(inspect_storage("https://example.supabase.co", "secret", opener=lambda *_a, **_kw: response))

    def test_bucket_restrictions_are_required(self):
        response = io.BytesIO(b'{"id":"canhotos","public":false}')
        result = " ".join(inspect_storage(
            "https://example.supabase.co", "secret", opener=lambda *_a, **_kw: response
        ))
        self.assertIn("5 MB", result)
        self.assertIn("JPEG", result)

    def test_never_leaks_db_connection_error(self):
        def failure(*_a, **_kw):
            raise RuntimeError("password=SUPERSECRET")
        self.assertEqual(inspect_database("hidden", connect=failure),
                         ["Falha na conexão ou consulta ao banco; confira os segredos no provedor"])


if __name__ == "__main__":
    unittest.main()
