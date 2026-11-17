import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock

from conrado.postgres_store import PostgresStore
from conrado.store import BusinessError


class FakePostgres(PostgresStore):
    def __init__(self, replies):
        super().__init__("postgresql://placeholder", MagicMock())
        self.replies = iter(replies)
        self.calls = []

    @contextmanager
    def connection(self):
        yield self

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        self._reply = next(self.replies, None)
        return self

    def fetchone(self):
        return self._reply


class PostgresStoreTests(unittest.TestCase):
    def test_constructor_requires_private_storage(self):
        with self.assertRaises(ValueError):
            PostgresStore("postgresql://placeholder", None)

    def test_reject_release_without_notes(self):
        store = FakePostgres([{"vehicle_id": 7, "status": "draft"}, None])
        with self.assertRaisesRegex(BusinessError, "sem notas"):
            store.release_trip(3)
        self.assertTrue(all("app." in sql for sql, _ in store.calls))

    def test_reject_release_if_another_trip_is_active(self):
        store = FakePostgres([{"vehicle_id": 7, "status": "draft"}, {"?": 1}, {"?": 1}])
        with self.assertRaisesRegex(BusinessError, "Já existe viagem liberada"):
            store.release_trip(3)

    def test_reject_close_with_pending_notes(self):
        store = FakePostgres([{"status": "released"}, {"total": 2}])
        with self.assertRaisesRegex(BusinessError, "2 NF-e"):
            store.close_driver_trip(5, 3)

    def test_no_write_when_delivery_lacks_photo(self):
        store = FakePostgres([])
        with self.assertRaisesRegex(BusinessError, "Fotografia do canhoto"):
            store.record_delivery(1, 2, "event-123456789", "delivered", "")
        self.assertEqual(store.calls, [])

    def test_duplicate_event_is_idempotent(self):
        store = FakePostgres([{"note_id": 2, "driver_id": 1}])
        result = store.record_delivery(1, 2, "event-123456789", "occurrence", "avaria")
        self.assertTrue(result["duplicate"])
        self.assertEqual(len(store.calls), 1)

    def test_summary_populates_missing_statuses(self):
        store = FakePostgres([])
        store.fetchall = lambda: []
        self.assertEqual(store.summary()["trips"]["released"], 0)
        self.assertEqual(store.summary()["notes"]["delivered"], 0)


if __name__ == "__main__":
    unittest.main()
