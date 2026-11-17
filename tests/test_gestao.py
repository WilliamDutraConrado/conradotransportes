import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from conrado.gestao import Server


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

    def request(self, path, body=None, authorized=True):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["Authorization"] = "Bearer segredo-para-teste-1234567890"
        request = Request(f"http://127.0.0.1:{self.server.server_port}{path}",
                          data=json.dumps(body).encode() if body is not None else None,
                          headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_requer_token_e_cadastra_motorista(self):
        status, _ = self.request("/api/drivers", authorized=False)
        self.assertEqual(status, 401)
        status, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC-1D23", "vehicle_type": "3/4"})
        self.assertEqual(status, 200)
        self.assertEqual(driver["plate"], "ABC1D23")
        status, result = self.request("/api/drivers")
        self.assertEqual(status, 200)
        self.assertEqual(len(result), 1)

    def test_nao_libera_viagem_sem_notas(self):
        _, driver = self.request("/api/drivers", {"name": "Motorista Teste", "plate": "ABC1D23", "vehicle_type": "3/4"})
        _, trip = self.request("/api/trips", {"vehicle_id": driver["vehicle_id"], "label": "Primeira carga"})
        status, _ = self.request(f"/api/trips/{trip['trip_id']}/release", {})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
