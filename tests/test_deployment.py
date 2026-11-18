import unittest
import json
import struct
from pathlib import Path

from conrado.version import VERSION


ROOT = Path(__file__).resolve().parent.parent


class DeploymentFilesTests(unittest.TestCase):
    def test_container_runs_non_root_and_has_healthcheck(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.13-slim", dockerfile)
        self.assertIn("USER conrado", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/readyz", dockerfile)
        self.assertIn("postgresql-client", dockerfile)
        self.assertIn("COPY infra ./infra", dockerfile)
        self.assertNotIn("SUPABASE_SECRET_KEY=", dockerfile)
        self.assertNotIn("DATABASE_URL=", dockerfile)

    def test_container_context_excludes_secrets_and_real_documents(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignored)
        self.assertIn("*.xml", ignored)
        self.assertIn("dados-reais", ignored)
        self.assertIn("evidencias", ignored)
        self.assertIn("*.backup", ignored)

    def test_ci_has_read_only_repository_permission(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("node --test tests/js/test_offline_queue.js", workflow)
        self.assertIn("python -m pip_audit --requirement requirements-postgres.txt --strict", workflow)

    def test_pwa_declares_offline_shell_without_caching_api(self):
        manifest = (ROOT / "web" / "manifest.webmanifest").read_text(encoding="utf-8")
        worker = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        driver = (ROOT / "web" / "assets" / "motorista.js").read_text(encoding="utf-8")
        queue = (ROOT / "web" / "assets" / "offline-queue.js").read_text(encoding="utf-8")
        self.assertIn('"display": "standalone"', manifest)
        self.assertIn('"start_url": "/motorista"', manifest)
        self.assertIn('url.pathname.startsWith("/api/")', worker)
        self.assertIn('navigator.serviceWorker.register("/sw.js"', driver)
        self.assertIn('name: "AES-GCM"', queue)

    def test_pwa_has_android_maskable_and_apple_icons(self):
        manifest = json.loads((ROOT / "web" / "manifest.webmanifest").read_text(encoding="utf-8"))
        icons = {item["src"]: item for item in manifest["icons"]}
        self.assertEqual(icons["/assets/icon-192.png"]["sizes"], "192x192")
        self.assertEqual(icons["/assets/icon-512.png"]["sizes"], "512x512")
        self.assertEqual(icons["/assets/icon-maskable-512.png"]["purpose"], "maskable")
        for name, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192),
                           ("icon-512.png", 512), ("icon-maskable-512.png", 512)):
            content = (ROOT / "web" / "assets" / name).read_bytes()
            self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", content[16:24]), (size, size))

    def test_version_is_consistent_in_release_assets(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), VERSION)
        self.assertIn(VERSION, (ROOT / "web" / "motorista.html").read_text(encoding="utf-8"))
        self.assertIn(VERSION, (ROOT / "web" / "gestao.html").read_text(encoding="utf-8"))
        self.assertIn(VERSION, (ROOT / "web" / "sw.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
