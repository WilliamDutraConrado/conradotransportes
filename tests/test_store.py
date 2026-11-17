import tempfile
import base64
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

    def test_indicadores_com_notas_reais_da_base(self):
        self.assertEqual(self.db.summary()["notes"]["pending"], 0)
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.assertEqual(self.db.summary()["trips"]["draft"], 1)
        self.assertEqual(self.db.summary()["notes"]["pending"], 1)
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        self.db.record_delivery(self.driver["driver_id"], note_id, "evento-1234567890", "occurrence", "Avaria")
        summary = self.db.summary()
        self.assertEqual(summary["trips"]["released"], 1)
        self.assertEqual(summary["notes"]["pending"], 0)
        self.assertEqual(summary["notes"]["occurrence"], 1)

    def test_detalhe_mantem_nfs_do_mesmo_destinatario_separadas(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Carga com duas notas")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX), parse_nfe(xml("2" * 44), REGEX)])
        detail = self.db.trip_detail(trip["trip_id"])
        self.assertEqual(len(detail["notes"]), 2)
        self.assertEqual(len({n["id"] for n in detail["notes"]}), 2)
        self.assertIsNone(self.db.trip_detail(99999))

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

    def test_login_motorista_e_registro_idempotente_ocorrencia(self):
        self.db.set_driver_password(self.driver["driver_id"], "SenhaIndividualSegura2026!")
        with self.assertRaises(BusinessError):
            self.db.authenticate_driver("ABC1D23", "senhaerrada")
        token = self.db.authenticate_driver("ABC-1D23", "SenhaIndividualSegura2026!")
        self.assertEqual(self.db.driver_by_token(token), self.driver["driver_id"])
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        event = "evento-1234567890"
        first = self.db.record_delivery(self.driver["driver_id"], note_id, event, "occurrence", "Produto avariado")
        second = self.db.record_delivery(self.driver["driver_id"], note_id, event, "occurrence", "Produto avariado")
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.db.driver_trip(self.driver["driver_id"])["notes"][0]["status"], "occurrence")
        self.db.set_driver_password(self.driver["driver_id"], "OutraSenhaSegura2026!")
        self.assertIsNone(self.db.driver_by_token(token))

    def test_nao_permite_baixa_entregue_sem_foto(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        with self.assertRaises(BusinessError):
            self.db.record_delivery(self.driver["driver_id"], note_id, "evento-1234567890", "delivered", "")

    def test_foto_obrigatoria_e_encerramento(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        with self.assertRaises(BusinessError):
            self.db.close_driver_trip(self.driver["driver_id"], trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"test-fixture" * 15).decode()
        result = self.db.record_delivery(self.driver["driver_id"], note_id, "evento-1234567890", "delivered", "", jpeg)
        self.assertEqual(result["status"], "delivered")
        with self.db.connection() as connection:
            evidence = connection.execute("SELECT sha256,content FROM evidence WHERE note_id=?", (note_id,)).fetchone()
            self.assertIsNotNone(evidence)
            self.assertTrue(evidence["content"].startswith(b"\xff\xd8\xff"))
        closed = self.db.close_driver_trip(self.driver["driver_id"], trip["trip_id"])
        self.assertEqual(closed["status"], "closed")
        self.assertIsNone(self.db.driver_trip(self.driver["driver_id"]))


if __name__ == "__main__":
    unittest.main()
