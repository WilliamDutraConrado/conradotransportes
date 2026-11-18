import tempfile
import unittest
from pathlib import Path

from conrado.release_check import PWA_FILES, inspect_release_configuration


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        for relative in PWA_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture", encoding="utf-8")
        self.environment = {
            "CONRADO_BIND": "0.0.0.0",
            "PORT": "8080",
            "CONRADO_ALLOWED_ORIGINS": "https://piloto.example",
            "CONRADO_ACCESS_LOG": "1",
            "CONRADO_ALLOW_ADMIN_TOKEN": "0",
            "CONRADO_RETENTION_ENABLED": "1",
            "DATABASE_URL": "postgresql://redacted",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SECRET_KEY": "secret-placeholder",
            "SUPABASE_PROJECT_REF": "example",
        }

    def tearDown(self):
        self.folder.cleanup()

    def test_hosted_configuration_is_approved(self):
        issues = inspect_release_configuration(
            self.environment, tool_resolver=lambda name: name, project_root=self.root,
        )
        self.assertEqual(issues, [])

    def test_rejects_local_bind_legacy_key_and_missing_tools(self):
        environment = dict(self.environment)
        environment.update({
            "CONRADO_BIND": "127.0.0.1", "CONRADO_ALLOWED_ORIGINS": "http://localhost:8766",
            "CONRADO_ALLOW_ADMIN_TOKEN": "1", "CONRADO_ACCESS_LOG": "0",
            "SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": "legacy-placeholder",
        })
        issues = " ".join(inspect_release_configuration(
            environment, tool_resolver=lambda _name: None, project_root=self.root,
        ))
        self.assertIn("0.0.0.0", issues)
        self.assertIn("service_role", issues)
        self.assertIn("pg_dump", issues)


if __name__ == "__main__":
    unittest.main()
