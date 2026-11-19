import unittest
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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

    def test_admin_actor_is_preserved_in_postgres_audit(self):
        store = FakePostgres([None, {"id": 11}, {"id": 22}, None])

        result = store.create_driver("Motorista", "ABC1D23", "TOCO", actor="admin:7")

        self.assertEqual(result["driver_id"], 11)
        audit_sql, audit_params = store.calls[-1]
        self.assertIn("INSERT INTO app.audit", audit_sql)
        self.assertEqual(audit_params[0], "admin:7")

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

    def test_close_retry_is_idempotent_after_uncertain_network_response(self):
        store = FakePostgres([{"status": "closed"}])

        result = store.close_driver_trip(5, 3)

        self.assertEqual(result, {"trip_id": 3, "status": "closed", "duplicate": True})
        self.assertEqual(len(store.calls), 1)

    def test_no_write_when_delivery_lacks_photo(self):
        store = FakePostgres([None, None])
        with self.assertRaisesRegex(BusinessError, "Fotografia do canhoto"):
            store.record_delivery(1, 2, "event-123456789", "delivered", "")
        self.assertEqual(len(store.calls), 2)
        self.assertIn("pg_advisory_xact_lock", store.calls[0][0])
        self.assertTrue(store.calls[1][0].lstrip().startswith("SELECT"))

    def test_duplicate_event_is_idempotent(self):
        store = FakePostgres([None, {
            "note_id": 2, "driver_id": 1, "status": "occurrence", "reason": "avaria",
            "arrival_time": "11:00", "affected_item": "Caixa", "seller_name": "Ian",
            "created_at": "2026-11-17T14:00:00+00:00", "number": "123", "recipient": "Cliente",
            "city": "Itaúna", "seller": "Ian", "plate": "ABC1D23", "driver": "Motorista",
        }])
        result = store.record_delivery(1, 2, "event-123456789", "occurrence", "avaria")
        self.assertTrue(result["duplicate"])
        self.assertIn("NF: 123", result["share_text"])
        self.assertEqual(len(store.calls), 2)

    def test_database_failure_removes_uploaded_evidence(self):
        store = FakePostgres([
            None,
            None,
            {"status": "pending", "number": "123", "recipient": "Cliente", "city": "Itaúna",
             "seller": "Ian", "trip_id": 3, "trip_status": "released", "plate": "ABC1D23",
             "driver": "Motorista"},
            None,
        ])
        uploaded = SimpleNamespace(
            path="viagens/3/notas/2/event-123456789-abcdef012345.jpg",
            mime="image/jpeg", sha256="a" * 64,
        )
        store.storage.upload.return_value = uploaded
        original_execute = store.execute

        def failing_execute(sql, params=()):
            if "INSERT INTO app.evidence" in sql:
                raise RuntimeError("falha de commit simulada")
            return original_execute(sql, params)

        store.execute = failing_execute
        photo = base64.b64encode(b"\xff\xd8\xff" + b"dados" * 30 + b"\xff\xd9").decode()

        with self.assertRaisesRegex(RuntimeError, "falha de commit simulada"):
            store.record_delivery(1, 2, "event-123456789", "delivered", "", photo)

        store.storage.delete.assert_called_once_with(uploaded.path)

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

    def test_evidence_access_returns_short_lived_signed_url(self):
        store = FakePostgres([{"storage_path": "viagens/3/notas/2/event-123456789-abcdef012345.jpg"}])
        store.storage.signed_download_url.return_value = "https://example.supabase.co/signed"

        result = store.evidence_access("event-123456789")

        self.assertEqual(result["expires_in"], 300)
        self.assertTrue(result["url"].startswith("https://"))
        store.storage.signed_download_url.assert_called_once_with(
            "viagens/3/notas/2/event-123456789-abcdef012345.jpg", 300
        )

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

    def test_revoke_admin_session_hashes_token(self):
        store = FakePostgres([])

        store.revoke_admin_session("token-admin-secreto")

        self.assertEqual(len(store.calls), 1)
        self.assertIn("DELETE FROM app.admin_sessions", store.calls[0][0])
        self.assertNotIn("token-admin-secreto", store.calls[0][1])

    def test_admin_identity_includes_role(self):
        store = FakePostgres([{"admin_id": 7, "username": "operador", "role": "operator"}])

        identity = store.admin_identity_by_token("token-admin-secreto")

        self.assertEqual(identity, {"admin_id": 7, "username": "operador", "role": "operator"})
        self.assertNotIn("token-admin-secreto", store.calls[0][1])

    def test_admin_users_serializes_postgres_timestamp(self):
        store = FakePostgres([[{
            "admin_id": 7, "username": "william", "role": "admin", "active": True,
            "created_at": "2026-11-19 06:14:00+00",
        }]])

        users = store.admin_users()

        self.assertEqual(users[0]["created_at"], "2026-11-19 06:14:00+00")
        self.assertIn("created_at::text AS created_at", store.calls[0][0])

    def test_admin_access_update_is_audited(self):
        store = FakePostgres([
            {"id": 8, "username": "auditor", "role": "auditor", "active": True},
            None,
            None,
        ])

        result = store.set_admin_access(8, "operator", True, actor="admin:7", current_admin_id=7)

        self.assertEqual(result["role"], "operator")
        self.assertEqual(store.calls[-1][1][0], "admin:7")

    def test_login_failure_uses_atomic_upsert_and_only_hashes(self):
        store = FakePostgres([None, None])

        store.record_login_failure("192.0.2.10", "ABC1D23")

        self.assertEqual(len(store.calls), 2)
        for sql, params in store.calls:
            self.assertIn("ON CONFLICT", sql)
            self.assertEqual(len(params[0]), 64)
            self.assertNotIn("192.0.2.10", params)
            self.assertNotIn("ABC1D23", params)

    def test_retention_preview_does_not_delete_storage(self):
        path = "viagens/3/notas/2/event-123456789-abcdef012345.jpg"
        store = FakePostgres([
            [{"id": 9, "storage_path": path}],
            {"total": 2}, {"total": 3}, {"total": 4}, {"total": 5},
        ])
        moment = datetime(2026, 11, 18, tzinfo=timezone.utc)

        result = store.cleanup_retention(
            now_at=moment, evidence_before=moment - timedelta(days=180),
            audit_before=moment - timedelta(days=730), apply=False,
        )

        self.assertFalse(result["applied"])
        self.assertEqual(result["candidates"], {
            "admin_sessions": 2, "driver_sessions": 3, "login_limits": 4,
            "evidence": 1, "audit": 5,
        })
        store.storage.delete.assert_not_called()

    def test_retention_deletes_private_object_before_database_row(self):
        path = "viagens/3/notas/2/event-123456789-abcdef012345.jpg"
        store = FakePostgres([
            [{"id": 9, "storage_path": path}],
            {"total": 0}, {"total": 0}, {"total": 0}, {"total": 0},
            None, None, None, None, None, None,
        ])
        moment = datetime(2026, 11, 18, tzinfo=timezone.utc)

        result = store.cleanup_retention(
            now_at=moment, evidence_before=moment - timedelta(days=180),
            audit_before=moment - timedelta(days=730), apply=True,
        )

        self.assertTrue(result["applied"])
        store.storage.delete.assert_called_once_with(path)
        self.assertTrue(any("DELETE FROM app.evidence" in sql for sql, _ in store.calls))


if __name__ == "__main__":
    unittest.main()
