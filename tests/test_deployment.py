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

    def test_render_blueprint_is_free_and_does_not_embed_secrets(self):
        blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("runtime: docker", blueprint)
        self.assertIn("plan: free", blueprint)
        self.assertIn("healthCheckPath: /readyz", blueprint)
        self.assertIn("value: https://conrado-entregas-william-dutra.onrender.com", blueprint)
        for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_PROJECT_REF"):
            self.assertIn(f"- key: {name}\n        sync: false", blueprint)
        self.assertNotIn("postgresql://", blueprint)
        self.assertNotIn("sb_secret_", blueprint)

    def test_pwa_declares_offline_shell_without_caching_api(self):
        manifest = (ROOT / "web" / "manifest.webmanifest").read_text(encoding="utf-8")
        worker = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
        driver = (ROOT / "web" / "assets" / "motorista.js").read_text(encoding="utf-8")
        delivery_form = (ROOT / "web" / "assets" / "delivery-form.js").read_text(encoding="utf-8")
        queue = (ROOT / "web" / "assets" / "offline-queue.js").read_text(encoding="utf-8")
        self.assertIn('"display": "standalone"', manifest)
        self.assertIn('"start_url": "/motorista"', manifest)
        self.assertIn('url.pathname.startsWith("/api/")', worker)
        self.assertIn('navigator.serviceWorker.register("/sw.js"', driver)
        self.assertIn('"/assets/delivery-form.js"', worker)
        self.assertIn("validateInput", delivery_form)
        self.assertIn('name: "AES-GCM"', queue)

    def test_public_branding_distinguishes_driver_and_operations_products(self):
        manifest = json.loads((ROOT / "web" / "manifest.webmanifest").read_text(encoding="utf-8"))
        driver = (ROOT / "web" / "motorista.html").read_text(encoding="utf-8")
        management = (ROOT / "web" / "gestao.html").read_text(encoding="utf-8")
        emblem = (ROOT / "web" / "assets" / "emblema-dutralog.webp").read_bytes()
        self.assertEqual(manifest["short_name"], "DutraLog")
        self.assertIn("DutraLog", driver)
        self.assertIn("DutraTracking", management)
        self.assertIn('/assets/emblema-dutralog.webp', driver)
        self.assertIn('/assets/emblema-dutralog.webp', management)
        self.assertEqual(emblem[:4], b"RIFF")
        self.assertEqual(emblem[8:12], b"WEBP")
        self.assertNotIn("Conrado Entregas", driver)
        self.assertNotIn("Conrado Entregas", management)

    def test_pwa_has_android_maskable_and_apple_icons(self):
        manifest = json.loads((ROOT / "web" / "manifest.webmanifest").read_text(encoding="utf-8"))
        icons = {item["src"]: item for item in manifest["icons"]}
        self.assertEqual(icons["/assets/dutralog-icon-192.png"]["sizes"], "192x192")
        self.assertEqual(icons["/assets/dutralog-icon-512.png"]["sizes"], "512x512")
        self.assertEqual(icons["/assets/dutralog-icon-maskable-512.png"]["purpose"], "maskable")
        for name, size in (("dutralog-apple-touch-icon.png", 180), ("dutralog-icon-192.png", 192),
                           ("dutralog-icon-512.png", 512), ("dutralog-icon-maskable-512.png", 512),
                           ("emblema-dutralog-master.png", 1024)):
            content = (ROOT / "web" / "assets" / name).read_bytes()
            self.assertEqual(content[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", content[16:24]), (size, size))

    def test_version_is_consistent_in_release_assets(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), VERSION)
        self.assertIn(VERSION, (ROOT / "web" / "motorista.html").read_text(encoding="utf-8"))
        self.assertIn(VERSION, (ROOT / "web" / "gestao.html").read_text(encoding="utf-8"))
        self.assertIn(VERSION, (ROOT / "web" / "sw.js").read_text(encoding="utf-8"))

    def test_driver_pin_policy_is_visible_in_both_interfaces(self):
        management = (ROOT / "web" / "gestao.html").read_text(encoding="utf-8")
        driver = (ROOT / "web" / "motorista.html").read_text(encoding="utf-8")
        script = (ROOT / "web" / "assets" / "gestao.js").read_text(encoding="utf-8")
        self.assertIn('pattern="[0-9]{5}"', management)
        self.assertIn("PIN individual", driver)
        self.assertIn("/^[0-9]{5}$/", script)


if __name__ == "__main__":
    unittest.main()
