import json
import base64
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from conrado.gestao import Server
from test_importer import xml


class APITest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.server = Server(("127.0.0.1", 0), "segredo-para-teste-1234567890", Path(self.folder.name) / "test.db")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def request(self, path, body=None, authorized=True, with_headers=False, auth_token=None):
        headers = {"Content-Type": "application/json"}
        if auth_token is not None:
            headers["Authorization"] = f"Bearer {auth_token}"
        elif authorized:
            headers["Authorization"] = "Bearer segredo-para-teste-1234567890"
        request = Request(f"http://127.0.0.1:{self.server.server_port}{path}",
                          data=json.dumps(body).encode() if body is not None else None,
                          headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                result = (response.status, json.load(response), dict(response.headers))
        except HTTPError as exc:
            result = (exc.code, json.load(exc), dict(exc.headers))
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
        self.assertEqual(self.request(f"/api/drivers/{driver['driver_id']}/password",
                                      {"password": "SenhaIndividualSegura2026!"})[0], 200)
        for _ in range(5):
            status, result = self.request("/api/driver/login", {"plate": "ABC1D23", "password": "incorreta"},
                                          authorized=False)
            self.assertEqual(status, 401)
            self.assertEqual(result["erro"], "Credenciais inválidas")
        status, result, headers = self.request("/api/driver/login",
                                               {"plate": "ABC1D23", "password": "incorreta"},
                                               authorized=False, with_headers=True)
        self.assertEqual(status, 429)
        self.assertEqual(headers["Retry-After"], "300")
        self.assertIn("cinco minutos", result["erro"])

    def test_respostas_incluem_cabecalhos_de_seguranca(self):
        status, _, headers = self.request("/api/drivers", with_headers=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_logout_revoga_sessao_do_motorista(self):
        _, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"})
        self.request(f"/api/drivers/{driver['driver_id']}/password", {"password": "SenhaIndividualSegura2026!"})
        status, login = self.request("/api/driver/login",
                                     {"plate": "ABC1D23", "password": "SenhaIndividualSegura2026!"},
                                     authorized=False)
        self.assertEqual(status, 200)
        token = login["token"]
        self.assertEqual(self.request("/api/driver/trip", authorized=False, auth_token=token)[0], 200)

        status, result = self.request("/api/driver/logout", {}, authorized=False, auth_token=token)

        self.assertEqual(status, 200)
        self.assertTrue(result["logged_out"])
        self.assertEqual(self.request("/api/driver/trip", authorized=False, auth_token=token)[0], 401)

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
