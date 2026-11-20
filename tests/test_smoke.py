import unittest

from conrado.smoke import run_smoke_test


class FakeResult:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class FakeConnection:
    def __init__(self, *, verification=False):
        self.verification = verification
        self.calls = []
        self.rolled_back = False
        self.next_id = 1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.verification:
            return FakeResult(False)
        value = self.next_id
        self.next_id += 1
        return FakeResult(value)

    def rollback(self):
        self.rolled_back = True


class SmokeTests(unittest.TestCase):
    def test_exercises_schema_and_confirms_rollback(self):
        transaction = FakeConnection()
        verification = FakeConnection(verification=True)
        connections = iter((transaction, verification))

        result = run_smoke_test("postgresql://redacted", connect=lambda *_a, **_kw: next(connections))

        self.assertTrue(transaction.rolled_back)
        self.assertTrue(result["rollback_confirmed"])
        self.assertEqual(result["tables_exercised"], 13)
        sql = " ".join(statement for statement, _ in transaction.calls)
        for table in ("drivers", "vehicles", "trips", "notes", "driver_auth", "driver_sessions",
                      "admin_users", "admin_sessions", "login_limits",
                      "delivery_starts", "delivery_events", "evidence", "audit"):
            self.assertIn("app." + table, sql)


if __name__ == "__main__":
    unittest.main()
