import json
import base64
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from conrado.gestao import Handler, Server, delivery_observation
from conrado.store import ServiceUnavailable
from conrado.version import VERSION
from test_importer import xml


class DeliveryObservationTests(unittest.TestCase):
    def test_receiver_is_preserved_without_prefilling_optional_observation(self):
        self.assertEqual(delivery_observation("delivered", "João", ""), "Recebedor: João")
        self.assertEqual(
            delivery_observation("delivered", "Ana", "Caixas conferidas"),
            "Recebedor: Ana\nCaixas conferidas",
        )

    def test_receiver_accepts_only_a_first_name(self):
        with self.assertRaisesRegex(Exception, "primeiro nome"):
            delivery_observation("delivered", "João da Silva", "")
        with self.assertRaisesRegex(Exception, "primeiro nome"):
            delivery_observation("refused", "João", "")


class APITest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.admin_token = "segredo-para-teste-1234567890-abcd"
        self.server = Server(("127.0.0.1", 0), self.admin_token, Path(self.folder.name) / "test.db")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def request(self, path, body=None, authorized=True, with_headers=False, auth_token=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        if auth_token is not None:
            headers["Authorization"] = f"Bearer {auth_token}"
        elif authorized:
            headers["Authorization"] = "Bearer " + self.admin_token
        request = Request(f"http://127.0.0.1:{self.server.server_port}{path}",
                          data=json.dumps(body).encode() if body is not None else None,
                          headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                result = (response.status, json.load(response), dict(response.headers))
        except HTTPError as exc:
            try:
                result = (exc.code, json.load(exc), dict(exc.headers))
            finally:
                exc.close()
        return result if with_headers else result[:2]

    def test_requer_token_e_cadastra_motorista(self):
        status, _ = self.request("/api/drivers", authorized=False)
        self.assertEqual(status, 401)
        status, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC-1D23", "vehicle_type": "3/4"})
        self.assertEqual(status, 200)
        self.assertEqual(driver["plate"], "ABC1D23")
        status, result = self.request("/api/drivers")
        self.assertEqual(status, 200)
        self.assertEqual(len(result), 1)
        status, productivity = self.request("/api/productivity")
        self.assertEqual(status, 200)
        self.assertEqual(productivity[0]["plate"], "ABC1D23")
        self.assertEqual(self.request("/api/productivity", authorized=False)[0], 401)

    def test_login_limita_tentativas_e_retorna_retry_after(self):
        _, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"})
        status, result = self.request(
            f"/api/drivers/{driver['driver_id']}/password", {"password": "12345"},
        )
        self.assertEqual(status, 400)
        self.assertIn("menos previsível", result["erro"])
        self.assertEqual(self.request(f"/api/drivers/{driver['driver_id']}/password",
                                      {"password": "48271"})[0], 200)
        for _ in range(5):
            status, result = self.request("/api/driver/login", {"plate": "ABC1D23", "password": "incorreta"},
                                          authorized=False)
            self.assertEqual(status, 401)
            self.assertEqual(result["erro"], "Credenciais inválidas")
        status, result, headers = self.request("/api/driver/login",
                                               {"plate": "ABC1D23", "password": "incorreta"},
                                               authorized=False, with_headers=True)
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "900")
        self.assertIn("quinze minutos", result["erro"])

    def test_falha_transitoria_da_foto_retorna_503_para_fila_offline(self):
        _, driver = self.request(
            "/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"},
        )
        self.request(f"/api/drivers/{driver['driver_id']}/password",
                     {"password": "48271"})
        _, login = self.request(
            "/api/driver/login", {"plate": "ABC1D23", "password": "48271"},
            authorized=False,
        )
        self.server.db.record_delivery = MagicMock(side_effect=ServiceUnavailable("Storage indisponível"))

        status, result, headers = self.request(
            "/api/driver/notes/1/status",
            {"event_id": "evento-storage-123", "status": "delivered"},
            authorized=False, auth_token=login["token"], with_headers=True,
        )

        self.assertEqual(status, 503)
        self.assertEqual(headers["Retry-After"], "30")
        self.assertEqual(result["erro"], "Storage indisponível")

    def test_respostas_incluem_cabecalhos_de_seguranca(self):
        status, _, headers = self.request("/api/drivers", with_headers=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertRegex(headers["X-Request-ID"], r"^[a-f0-9]{16}$")
        self.assertNotIn("Python", headers["Server"])
        self.assertEqual(headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(headers["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertIn("geolocation=(self)", headers["Permissions-Policy"])
        self.assertIn("microphone=()", headers["Permissions-Policy"])

    def test_healthcheck_e_readiness_nao_exigem_token(self):
        status, health = self.request("/healthz", authorized=False)
        self.assertEqual((status, health["status"]), (200, "ok"))
        self.assertEqual(health["version"], VERSION)

        status, version = self.request("/version.json", authorized=False)
        self.assertEqual((status, version["version"]), (200, VERSION))

        status, readiness = self.request("/readyz", authorized=False)
        self.assertEqual((status, readiness["status"]), (200, "ready"))

        self.server.db.healthcheck = MagicMock(side_effect=RuntimeError("segredo que não pode vazar"))
        status, readiness = self.request("/readyz", authorized=False)
        self.assertEqual((status, readiness["status"]), (503, "unavailable"))
        self.assertNotIn("segredo", json.dumps(readiness))

    def test_arquivos_pwa_sao_servidos_com_escopo_e_tipos_corretos(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        with urlopen(base + "/manifest.webmanifest", timeout=5) as response:
            manifest = json.load(response)
            self.assertEqual(response.headers.get_content_type(), "application/manifest+json")
        self.assertEqual(manifest["start_url"], "/motorista")
        with urlopen(base + "/sw.js", timeout=5) as response:
            worker = response.read().decode()
            self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
            self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertIn("/assets/motorista.js", worker)
        self.assertIn("/assets/delivery-form.js", worker)
        with urlopen(base + "/assets/delivery-form.js", timeout=5) as response:
            delivery_form = response.read().decode()
            self.assertEqual(response.headers.get_content_type(), "text/javascript")
            self.assertEqual(response.headers["Cache-Control"], "no-cache")
        self.assertIn("validateInput", delivery_form)
        with urlopen(base + "/motorista", timeout=5) as response:
            html = response.read().decode()
            self.assertNotIn("'unsafe-inline'", response.headers["Content-Security-Policy"])
        self.assertIn("manifest.webmanifest", html)
        for asset in ("dutralog-apple-touch-icon.png", "dutralog-icon-192.png",
                      "dutralog-icon-512.png", "dutralog-icon-maskable-512.png"):
            with urlopen(base + "/assets/" + asset, timeout=5) as response:
                self.assertEqual(response.headers.get_content_type(), "image/png")
                self.assertEqual(response.read(8), b"\x89PNG\r\n\x1a\n")
        with urlopen(base + "/assets/emblema-dutralog.webp", timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "image/webp")
            emblem = response.read(12)
            self.assertEqual(emblem[:4], b"RIFF")
            self.assertEqual(emblem[8:12], b"WEBP")

    def test_origem_configuravel_e_origem_desconhecida_bloqueada(self):
        self.server.allowed_origins.add("https://piloto.example")
        body = {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"}
        self.assertEqual(self.request("/api/drivers", body, origin="https://piloto.example")[0], 200)
        self.assertEqual(self.request("/api/trips", {"vehicle_id": 1, "label": "Teste"},
                                      origin="https://malicioso.example")[0], 403)

    def test_falha_interna_retorna_erro_generico_com_id(self):
        self.server.db.drivers = MagicMock(side_effect=RuntimeError("credencial-sensivel-simulada"))

        status, result = self.request("/api/drivers")

        self.assertEqual(status, 500)
        self.assertEqual(result["erro"], "Falha interna")
        self.assertRegex(result["request_id"], r"^[a-f0-9]{16}$")
        self.assertNotIn("credencial-sensivel", json.dumps(result))

    def test_acesso_ao_canhoto_exige_admin_e_nao_expoe_conteudo_direto(self):
        event_id = "evento-1234567890"
        self.server.db.evidence_access = MagicMock(return_value={
            "url": "https://example.supabase.co/signed", "expires_in": 300,
        })

        self.assertEqual(self.request(f"/api/evidence/{event_id}/access", authorized=False)[0], 401)
        status, result = self.request(f"/api/evidence/{event_id}/access")

        self.assertEqual(status, 200)
        self.assertEqual(result["expires_in"], 300)
        self.assertNotIn("content", result)

    def test_logout_revoga_sessao_do_motorista(self):
        _, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"})
        self.request(f"/api/drivers/{driver['driver_id']}/password", {"password": "48271"})
        status, login = self.request("/api/driver/login",
                                     {"plate": "ABC1D23", "password": "48271"},
                                     authorized=False)
        self.assertEqual(status, 200)
        token = login["token"]
        self.assertEqual(self.request("/api/driver/trip", authorized=False, auth_token=token)[0], 200)

        status, result = self.request("/api/driver/logout", {}, authorized=False, auth_token=token)

        self.assertEqual(status, 200)
        self.assertTrue(result["logged_out"])
        self.assertEqual(self.request("/api/driver/trip", authorized=False, auth_token=token)[0], 401)

    def test_inicio_baixa_foto_ocultacao_e_estorno_preservam_historico(self):
        _, driver = self.request(
            "/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"},
        )
        self.request(f"/api/drivers/{driver['driver_id']}/password",
                     {"password": "48271"})
        _, trip = self.request("/api/trips", {"vehicle_id": driver["vehicle_id"], "label": "Rota teste"})
        payload = {"arquivos": [{
            "name": "nf.xml", "base64": base64.b64encode(xml("1" * 44)).decode(),
        }]}
        self.assertEqual(self.request(f"/api/trips/{trip['trip_id']}/import", payload)[0], 200)
        self.assertEqual(self.request(f"/api/trips/{trip['trip_id']}/release", {})[0], 200)
        _, login = self.request(
            "/api/driver/login", {"plate": "ABC1D23", "password": "48271"},
            authorized=False,
        )
        token = login["token"]
        _, route = self.request("/api/driver/trip", authorized=False, auth_token=token)
        note_id = route["trip"]["notes"][0]["id"]
        started_at = datetime.now(timezone.utc).isoformat()
        status, started = self.request(
            f"/api/driver/notes/{note_id}/start",
            {"start_id": "inicio-api-123456", "started_at": started_at,
             "latitude": -20.145, "longitude": -44.883, "accuracy_m": 18},
            authorized=False, auth_token=token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(started["location_recorded"])
        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"canhoto-ficticio" * 10 + b"\xff\xd9").decode()
        status, delivery = self.request(
            f"/api/driver/notes/{note_id}/status",
            {"event_id": "evento-api-123456", "status": "delivered", "reason": "",
             "photo_base64": jpeg, "start_id": started["start_id"],
             "completed_at": datetime.now(timezone.utc).isoformat()},
            authorized=False, auth_token=token,
        )
        self.assertEqual((status, delivery["status"]), (200, "delivered"))
        _, route = self.request("/api/driver/trip", authorized=False, auth_token=token)
        self.assertEqual(route["trip"]["notes"], [])

        _, detail = self.request(f"/api/trips/{trip['trip_id']}/detail")
        self.assertEqual(detail["notes"][0]["events"][0]["latitude"], -20.145)
        self.assertIsNotNone(detail["notes"][0]["events"][0]["duration_seconds"])
        status, reopened = self.request(
            f"/api/notes/{note_id}/reopen", {"reason": "Canhoto anexado à nota incorreta"},
        )
        self.assertEqual((status, reopened["status"]), (200, "pending"))
        _, route = self.request("/api/driver/trip", authorized=False, auth_token=token)
        self.assertEqual([note["id"] for note in route["trip"]["notes"]], [note_id])
        _, detail = self.request(f"/api/trips/{trip['trip_id']}/detail")
        self.assertEqual(len(detail["notes"][0]["events"]), 1)
        self.assertEqual(len(detail["notes"][0]["reversals"]), 1)

    def test_login_administrativo_individual_e_logout(self):
        admin = self.server.db.create_admin("gestor", "SenhaAdministrativaSegura!")
        status, login = self.request(
            "/api/admin/login", {"username": "gestor", "password": "SenhaAdministrativaSegura!"},
            authorized=False,
        )
        self.assertEqual(status, 200)
        session = login["token"]
        self.assertEqual(login["user"]["role"], "admin")
        self.assertEqual(self.request("/api/admin/me", authorized=False, auth_token=session)[1]["username"],
                         "gestor")
        self.assertEqual(self.request("/api/drivers", authorized=False, auth_token=session)[0], 200)
        status, driver = self.request(
            "/api/drivers",
            {"name": "Motorista Auditado", "plate": "AUD1T23", "vehicle_type": "3/4"},
            authorized=False,
            auth_token=session,
        )
        self.assertEqual(status, 200)
        with self.server.db.connection() as db:
            audit = db.execute(
                "SELECT actor FROM audit WHERE object_type='driver' AND object_id=?",
                (driver["driver_id"],),
            ).fetchone()
        self.assertEqual(audit["actor"], f"admin:{admin['admin_id']}")

        status, result = self.request("/api/admin/logout", {}, authorized=False, auth_token=session)

        self.assertEqual(status, 200)
        self.assertTrue(result["logged_out"])
        self.assertEqual(self.request("/api/drivers", authorized=False, auth_token=session)[0], 401)

    def test_perfis_restringem_escrita_e_gestao_de_usuarios(self):
        admin = self.server.db.create_admin("gestor", "SenhaAdministrativaSegura!", "admin")
        operator = self.server.db.create_admin("operador", "SenhaOperadorSegura2026!", "operator")
        auditor = self.server.db.create_admin("auditor", "SenhaAuditorSegura2026!", "auditor")

        def login(username, password):
            status, result = self.request(
                "/api/admin/login", {"username": username, "password": password}, authorized=False,
            )
            self.assertEqual(status, 200)
            return result["token"]

        operator_token = login("operador", "SenhaOperadorSegura2026!")
        status, driver = self.request(
            "/api/drivers", {"name": "Motorista Operador", "plate": "OPE1R23", "vehicle_type": "TOCO"},
            authorized=False, auth_token=operator_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.request(
            f"/api/drivers/{driver['driver_id']}/password", {"password": "58271"},
            authorized=False, auth_token=operator_token,
        )[0], 403)
        self.assertEqual(self.request(
            "/api/admin/users", {"username": "novo", "password": "NovaSenhaAdministrativa!", "role": "auditor"},
            authorized=False, auth_token=operator_token,
        )[0], 403)

        auditor_token = login("auditor", "SenhaAuditorSegura2026!")
        self.assertEqual(self.request("/api/drivers", authorized=False, auth_token=auditor_token)[0], 200)
        self.assertEqual(self.request(
            "/api/trips", {"vehicle_id": driver["vehicle_id"], "label": "Proibida"},
            authorized=False, auth_token=auditor_token,
        )[0], 403)

        admin_token = login("gestor", "SenhaAdministrativaSegura!")
        status, created = self.request(
            "/api/admin/users",
            {"username": "conferencia", "password": "SenhaConferenciaSegura!", "role": "auditor"},
            authorized=False, auth_token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["role"], "auditor")
        status, users = self.request("/api/admin/users", authorized=False, auth_token=admin_token)
        self.assertEqual(status, 200)
        self.assertIn("conferencia", {user["username"] for user in users})
        self.assertEqual(self.request(
            f"/api/admin/users/{created['admin_id']}/access", {"role": "auditor", "active": False},
            authorized=False, auth_token=admin_token,
        )[0], 200)
        self.assertNotEqual(admin["admin_id"], operator["admin_id"])
        self.assertNotEqual(operator["admin_id"], auditor["admin_id"])

    def test_diagnostico_e_exportacoes_respeitam_perfis_e_csv_seguro(self):
        self.server.db.create_admin("operador", "SenhaOperadorSegura2026!", "operator")
        self.server.db.create_admin("auditor", "SenhaAuditorSegura2026!", "auditor")

        def login(username, password):
            return self.request(
                "/api/admin/login", {"username": username, "password": password}, authorized=False,
            )[1]["token"]

        operator_token = login("operador", "SenhaOperadorSegura2026!")
        auditor_token = login("auditor", "SenhaAuditorSegura2026!")
        self.assertEqual(self.request(
            "/api/admin/diagnostics", authorized=False, auth_token=operator_token,
        )[0], 403)
        status, diagnostic = self.request(
            "/api/admin/diagnostics", authorized=False, auth_token=auditor_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(diagnostic["version"], VERSION)
        self.assertNotIn("DATABASE_URL", json.dumps(diagnostic))

        headers = {"Authorization": f"Bearer {auditor_token}"}
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/admin/export/audit.csv", headers=headers,
        )
        with urlopen(request, timeout=5) as response:
            content = response.read().decode("utf-8-sig")
            self.assertEqual(response.headers.get_content_type(), "text/csv")
            self.assertIn("auditoria-dutratracking.csv", response.headers["Content-Disposition"])
        self.assertIn("Data/hora UTC", content)
        self.assertEqual(Handler._csv_cell("=SUM(A1:A2)"), "'=SUM(A1:A2)")

        operator_request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/admin/export/audit.csv",
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(operator_request, timeout=5)
        self.assertEqual(context.exception.code, 403)
        context.exception.close()

    def test_chave_estatica_pode_ser_desabilitada(self):
        self.server.allow_admin_token = False
        self.assertEqual(self.request("/api/drivers")[0], 401)

    def test_chave_estatica_e_identificada_como_legada_na_auditoria(self):
        status, driver = self.request(
            "/api/drivers", {"name": "Motorista Legado", "plate": "LEG1A23", "vehicle_type": "TOCO"},
        )
        self.assertEqual(status, 200)
        with self.server.db.connection() as db:
            audit = db.execute(
                "SELECT actor FROM audit WHERE object_type='driver' AND object_id=?",
                (driver["driver_id"],),
            ).fetchone()
        self.assertEqual(audit["actor"], "admin:legacy")

    def test_nao_libera_viagem_sem_notas(self):
        _, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"})
        _, trip = self.request("/api/trips", {"vehicle_id": driver["vehicle_id"], "label": "Primeira carga"})
        status, _ = self.request(f"/api/trips/{trip['trip_id']}/release", {})
        self.assertEqual(status, 400)

    def test_fluxo_completo_importar_e_liberar(self):
        _, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"})
        _, trip = self.request("/api/trips", {"vehicle_id": driver["vehicle_id"], "label": "Primeira carga"})
        payload = {"arquivos": [{"name": "teste.xml", "base64": base64.b64encode(xml("1" * 44)).decode()}]}
        status, result = self.request(f"/api/trips/{trip['trip_id']}/import", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["imported"], 1)
        status, result = self.request(f"/api/trips/{trip['trip_id']}/release", {})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "released")

    def test_api_rejeita_xml_malformado_e_entidade_sem_importacao_parcial(self):
        _, driver = self.request(
            "/api/drivers", {"name": "Motorista XML", "plate": "XML1A23", "vehicle_type": "3/4"},
        )
        _, trip = self.request(
            "/api/trips", {"vehicle_id": driver["vehicle_id"], "label": "XML inválido"},
        )
        malicious = b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><foo>&x;</foo>'
        payload = {"arquivos": [
            {"name": "valida.xml", "base64": base64.b64encode(xml("9" * 44, "XML1A23")).decode()},
            {"name": "entidade.xml", "base64": base64.b64encode(malicious).decode()},
        ], "placa_regex": "(a+)+$"}

        status, error = self.request(f"/api/trips/{trip['trip_id']}/import", payload)

        self.assertEqual(status, 400)
        self.assertIn("DTD/ENTITY", error["erro"])
        self.assertEqual(self.request("/api/trips")[1][0]["notes"], 0)

    def test_detalhes_exigem_administrador_e_preservam_nfs_individuais(self):
        _, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"})
        _, trip = self.request("/api/trips", {"vehicle_id": driver["vehicle_id"], "label": "Carga de teste"})
        payload = {"arquivos": [
            {"name": "nf1.xml", "base64": base64.b64encode(xml("1" * 44)).decode()},
            {"name": "nf2.xml", "base64": base64.b64encode(xml("2" * 44)).decode()},
        ]}
        self.assertEqual(self.request(f"/api/trips/{trip['trip_id']}/import", payload)[0], 200)
        url = f"/api/trips/{trip['trip_id']}/detail"
        self.assertEqual(self.request(url, authorized=False)[0], 401)
        status, detail = self.request(url)
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["notes"]), 2)
        self.assertEqual(len({n["id"] for n in detail["notes"]}), 2)
        self.assertEqual({n["status"] for n in detail["notes"]}, {"pending"})
        self.assertEqual(self.request("/api/trips/99999/detail")[0], 404)


if __name__ == "__main__":
    unittest.main()
