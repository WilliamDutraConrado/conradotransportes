import tempfile
import unittest
from pathlib import Path

from conrado.importer import parse_nfe
from conrado.store import BusinessError, Store
from test_importer import REGEX, xml


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.folder.name) / "teste.sqlite")
        self.driver = self.db.create_driver("Motorista Teste", "ABC-1D23", "3/4")

    def tearDown(self):
        self.folder.cleanup()

    def test_cadastro_normaliza_placa_e_rejeita_duplicada(self):
        self.assertEqual(self.db.drivers()[0]["plate"], "ABC1D23")
        with self.assertRaises(BusinessError):
            self.db.create_driver("Outro", "ABC1D23", "HR")

    def test_uma_viagem_liberada_por_veiculo(self):
        first = self.db.create_trip(self.driver["vehicle_id"], "Segunda-feira")
        second = self.db.create_trip(self.driver["vehicle_id"], "Quarta-feira")
        note = parse_nfe(xml("1" * 44), REGEX)
        self.db.import_notes(first["trip_id"], [note])
        self.db.import_notes(second["trip_id"], [parse_nfe(xml("2" * 44), REGEX)])
        self.assertEqual(self.db.release_trip(first["trip_id"])["status"], "released")
        with self.assertRaises(BusinessError):
            self.db.release_trip(second["trip_id"])

    def test_nao_importa_placa_divergente_ou_nota_duplicada(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        other_plate = parse_nfe(xml("1" * 44, "DEF2G34"), REGEX)
        with self.assertRaises(BusinessError):
            self.db.import_notes(trip["trip_id"], [other_plate])
        note = parse_nfe(xml("1" * 44), REGEX)
        with self.assertRaises(BusinessError):
            self.db.import_notes(trip["trip_id"], [note, note])
        self.assertEqual(self.db.trips()[0]["notes"], 0)

    def test_erro_na_importacao_reverte_transacao(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        first = parse_nfe(xml("1" * 44), REGEX)
        second = parse_nfe(xml("2" * 44, "DEF2G34"), REGEX)
        with self.assertRaises(BusinessError):
            self.db.import_notes(trip["trip_id"], [first, second])
        self.assertEqual(self.db.trips()[0]["notes"], 0)


if __name__ == "__main__":
    unittest.main()
