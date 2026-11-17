import base64
import unittest

from conrado.painel import inspect_uploads
from test_importer import xml


class PainelTest(unittest.TestCase):
    def test_agrupa_nf_por_placa_e_parada(self):
        arquivos = [
            {"name": f"nota{i}.xml", "base64": base64.b64encode(xml(str(i) * 44)).decode()}
            for i in (1, 2)
        ]
        result = inspect_uploads(arquivos, r"PLACA: ([A-Z]{3}[0-9][A-Z0-9][0-9]{2})")
        self.assertEqual(result["total_notas"], 2)
        self.assertEqual(len(result["cargas"]), 1)
        self.assertEqual(len(result["cargas"][0]["paradas"]), 1)
        self.assertEqual(result["cargas"][0]["volumes"], 6)

    def test_arquivo_com_problema_nao_interrompe_lote(self):
        arquivos = [
            {"name": "valido.xml", "base64": base64.b64encode(xml("1" * 44)).decode()},
            {"name": "quebrado.xml", "base64": "!!!"},
        ]
        result = inspect_uploads(arquivos, r"PLACA: ([A-Z]{3}[0-9][A-Z0-9][0-9]{2})")
        self.assertEqual(result["total_notas"], 1)
        self.assertEqual(len(result["erros"]), 1)


if __name__ == "__main__":
    unittest.main()
