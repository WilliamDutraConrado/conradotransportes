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

    def fetchall(self):
        return self._reply or []


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
        store = FakePostgres([[], []])
        self.assertEqual(store.summary()["trips"]["released"], 0)
        self.assertEqual(store.summary()["notes"]["delivered"], 0)

    def test_trip_detail_includes_event_history(self):
        store = FakePostgres([
            {"id": 3, "label": "Carga", "status": "released", "plate": "ABC1D23", "driver": "Motorista"},
            [{"id": 8, "number": "123", "recipient": "Cliente", "address": "Rua 1",
              "status": "occurrence", "gross_weight": 10, "volumes": 2}],
            [{"id": "event-123456789", "note_id": 8, "status": "occurrence", "reason": "Avaria",
              "created_at": "2026-11-17 12:00:00+00", "driver": "Motorista", "has_evidence": False}],
        ])

        detail = store.trip_detail(3)

        self.assertEqual(detail["notes"][0]["events"][0]["reason"], "Avaria")
        self.assertNotIn("note_id", detail["notes"][0]["events"][0])
        self.assertIn("app.delivery_events", store.calls[2][0])

    def test_driver_productivity_calculates_delivery_rate(self):
        store = FakePostgres([[{
            "driver_id": 1, "name": "Motorista", "plate": "ABC1D23", "trips": 2,
            "in_route": 1, "closed_trips": 1, "notes": 5, "pending": 1,
            "delivered": 3, "issues": 1,
        }]])

        metric = store.driver_productivity()[0]

        self.assertEqual(metric["delivery_rate"], 60.0)
        self.assertIn("app.drivers", store.calls[0][0])

    def test_revoke_driver_session_hashes_token(self):
        store = FakePostgres([])

        store.revoke_driver_session("token-secreto")

        self.assertEqual(len(store.calls), 1)
        self.assertIn("DELETE FROM app.driver_sessions", store.calls[0][0])
        self.assertNotIn("token-secreto", store.calls[0][1])


if __name__ == "__main__":
    unittest.main()
