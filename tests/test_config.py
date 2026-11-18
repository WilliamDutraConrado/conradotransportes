import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conrado.config import ConfigError, load_env_file, server_settings, supabase_server_key


class ConfigTests(unittest.TestCase):
    def test_missing_file_is_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(load_env_file(Path(folder) / ".env"), 0)

    def test_loads_values_without_expansion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("CONRADO_TEST_VALUE='$NOT_EXPANDED'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_env_file(path), 1)
                self.assertEqual(os.environ["CONRADO_TEST_VALUE"], "$NOT_EXPANDED")

    def test_environment_has_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("CONRADO_TEST_VALUE=arquivo\n", encoding="utf-8")
            with patch.dict(os.environ, {"CONRADO_TEST_VALUE": "processo"}, clear=True):
                self.assertEqual(load_env_file(path), 0)
                self.assertEqual(os.environ["CONRADO_TEST_VALUE"], "processo")

    def test_rejects_malformed_line_without_showing_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("SEGREDO_SEM_IGUAL\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Linha 1 inválida"):
                load_env_file(path)

    def test_server_local_defaults(self):
        settings = server_settings({})
        self.assertEqual((settings.bind, settings.port), ("127.0.0.1", 8766))
        self.assertIn("http://localhost:8766", settings.allowed_origins)
        self.assertFalse(settings.access_log)
        self.assertTrue(settings.allow_admin_token)

    def test_external_bind_requires_https_origin(self):
        with self.assertRaisesRegex(ConfigError, "ALLOWED_ORIGINS"):
            server_settings({"CONRADO_BIND": "0.0.0.0"})
        with self.assertRaisesRegex(ConfigError, "origem inválida"):
            server_settings({
                "CONRADO_BIND": "0.0.0.0",
                "CONRADO_ALLOWED_ORIGINS": "http://piloto.example",
            })

        settings = server_settings({
            "CONRADO_BIND": "0.0.0.0", "PORT": "8080",
            "CONRADO_ALLOWED_ORIGINS": "https://piloto.example/",
            "CONRADO_ACCESS_LOG": "1",
        })
        self.assertEqual(settings.port, 8080)
        self.assertEqual(settings.allowed_origins, frozenset({"https://piloto.example"}))
        self.assertTrue(settings.access_log)
        self.assertFalse(settings.allow_admin_token)

    def test_server_rejects_invalid_port_without_echoing_value(self):
        with self.assertRaisesRegex(ConfigError, "PORT precisa") as context:
            server_settings({"PORT": "segredo-invalido"})
        self.assertNotIn("segredo-invalido", str(context.exception))

    def test_supabase_secret_key_prefers_current_and_accepts_legacy_transition(self):
        self.assertEqual(supabase_server_key({
            "SUPABASE_SECRET_KEY": "nova", "SUPABASE_SERVICE_ROLE_KEY": "legada",
        }), "nova")
        self.assertEqual(supabase_server_key({"SUPABASE_SERVICE_ROLE_KEY": "legada"}), "legada")
        self.assertEqual(supabase_server_key({}), "")


if __name__ == "__main__":
    unittest.main()
