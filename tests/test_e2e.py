import unittest

from conrado.e2e import synthetic_nfe, synthetic_png
from conrado.importer import parse_nfe
from conrado.painel import DEFAULT_REGEX
from conrado.supabase_storage import detect_image


class E2ETests(unittest.TestCase):
    def test_xml_ficticio_exercita_campos_da_ocorrencia(self):
        note = parse_nfe(synthetic_nfe("9" * 44, "TST0A01"), DEFAULT_REGEX)

        self.assertEqual(note["placa"], "TST0A01")
        self.assertEqual(note["destinatario"], "CLIENTE FICTÍCIO E2E")
        self.assertEqual(note["cidade"], "ITAÚNA")
        self.assertEqual(note["vendedor"], "IAN TESTE")
        self.assertEqual(note["itens"][0]["codigo"], "1940")
        self.assertEqual(note["peso_bruto_kg"], 1.5)

    def test_canhoto_ficticio_e_png_valido(self):
        image = synthetic_png()

        self.assertGreaterEqual(len(image), 100)
        self.assertEqual(detect_image(image), ("image/png", ".png"))


if __name__ == "__main__":
    unittest.main()
