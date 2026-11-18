import unittest
from datetime import datetime, timezone

from conrado.config import ConfigError
from conrado.maintenance import RetentionSettings, retention_settings, run_retention


class FakeStore:
    def __init__(self):
        self.arguments = None

    def cleanup_retention(self, **arguments):
        self.arguments = arguments
        return {"applied": arguments["apply"], "candidates": {}, "deleted": {}}


class MaintenanceTests(unittest.TestCase):
    def test_policy_is_disabled_by_default_and_has_conservative_periods(self):
        settings = retention_settings({})
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.evidence_days, 180)
        self.assertEqual(settings.audit_days, 730)

    def test_policy_rejects_dangerously_short_or_invalid_periods(self):
        with self.assertRaises(ConfigError):
            retention_settings({"CONRADO_EVIDENCE_RETENTION_DAYS": "7"})
        with self.assertRaises(ConfigError):
            retention_settings({"CONRADO_RETENTION_ENABLED": "yes"})

    def test_preview_and_apply_use_explicit_cutoffs(self):
        store = FakeStore()
        moment = datetime(2026, 11, 18, tzinfo=timezone.utc)
        settings = RetentionSettings(True, evidence_days=180, audit_days=730, interval_hours=24)

        result = run_retention(store, settings, apply=False, now_at=moment)

        self.assertFalse(result["applied"])
        self.assertFalse(store.arguments["apply"])
        self.assertEqual((moment - store.arguments["evidence_before"]).days, 180)
        self.assertEqual((moment - store.arguments["audit_before"]).days, 730)


if __name__ == "__main__":
    unittest.main()
