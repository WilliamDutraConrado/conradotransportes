import tempfile
import base64
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from conrado.importer import parse_nfe
from conrado.store import BusinessError, Store, occurrence_message
from test_importer import REGEX, xml


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.folder.name) / "teste.sqlite")
        self.driver = self.db.create_driver("Motorista Teste", "ABC-1D23", "3/4")

    def tearDown(self):
        self.folder.cleanup()

    def start_note(self, note_id, suffix="01", *, latitude=None, longitude=None, accuracy_m=None):
        start_id = f"inicio-teste-{suffix}"
        result = self.db.start_unloading(
            self.driver["driver_id"], note_id, start_id, datetime.now(timezone.utc).isoformat(),
            latitude, longitude, accuracy_m,
        )
        return result["start_id"]

    def test_cadastro_normaliza_placa_e_rejeita_duplicada(self):
        self.assertEqual(self.db.drivers()[0]["plate"], "ABC1D23")
        with self.assertRaises(BusinessError):
            self.db.create_driver("Outro", "ABC1D23", "HR")

    @patch("conrado.store.ZoneInfo", side_effect=ZoneInfoNotFoundError)
    def test_ocorrencia_tem_fallback_de_fuso_no_windows(self, _zoneinfo):
        message = occurrence_message(
            {"plate": "ABC1D23", "recipient": "Cliente", "number": "123", "city": "Itaúna",
             "seller": "Vendedor", "driver": "Motorista"},
            "occurrence", "Falta de produto", datetime(2026, 11, 17, 14, tzinfo=timezone.utc), "", "", "",
        )

        self.assertIn("Chegada no cliente: 11:00", message)
        self.assertIn("Data: 17/11", message)

    def test_indicadores_com_notas_reais_da_base(self):
        self.assertEqual(self.db.summary()["notes"]["pending"], 0)
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.assertEqual(self.db.summary()["trips"]["draft"], 1)
        self.assertEqual(self.db.summary()["notes"]["pending"], 1)
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        start_id = self.start_note(note_id, "indicadores")
        self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-1234567890", "occurrence", "Avaria",
            start_id=start_id, completed_at=datetime.now(timezone.utc).isoformat(), occurrence_type="general",
        )
        summary = self.db.summary()
        self.assertEqual(summary["trips"]["released"], 1)
        self.assertEqual(summary["notes"]["pending"], 0)
        self.assertEqual(summary["notes"]["occurrence"], 1)

    def test_produtividade_separa_entregas_pendencias_e_ocorrencias(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Rota de produtividade")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX), parse_nfe(xml("2" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        start_id = self.start_note(note_id, "produtividade")
        self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-produtividade", "occurrence", "Avaria",
            start_id=start_id, completed_at=datetime.now(timezone.utc).isoformat(), occurrence_type="general",
        )

        metric = self.db.driver_productivity()[0]

        self.assertEqual(metric["trips"], 1)
        self.assertEqual(metric["in_route"], 1)
        self.assertEqual(metric["notes"], 2)
        self.assertEqual(metric["pending"], 1)
        self.assertEqual(metric["issues"], 1)
        self.assertEqual(metric["delivered"], 0)
        self.assertEqual(metric["delivery_rate"], 0.0)

    def test_detalhe_mantem_nfs_do_mesmo_destinatario_separadas(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Carga com duas notas")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX), parse_nfe(xml("2" * 44), REGEX)])
        detail = self.db.trip_detail(trip["trip_id"])
        self.assertEqual(len(detail["notes"]), 2)
        self.assertEqual(len({n["id"] for n in detail["notes"]}), 2)
        self.assertEqual(detail["notes"][0]["events"], [])
        self.assertIsNone(self.db.trip_detail(99999))

    def test_rota_ordena_nfs_numericamente_e_exibe_atendimento_ativo(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Carga em ordem de canhoteira")
        notes = []
        for key, number in (("1" * 44, "10"), ("2" * 44, "2"), ("3" * 44, "1")):
            note = parse_nfe(xml(key), REGEX)
            note["numero"] = number
            notes.append(note)
        self.db.import_notes(trip["trip_id"], notes)
        self.db.release_trip(trip["trip_id"])

        route = self.db.driver_trip(self.driver["driver_id"])
        self.assertEqual([note["number"] for note in route["notes"]], ["1", "2", "10"])
        first_id = route["notes"][0]["id"]
        start_id = self.start_note(first_id, "ordem-ativa", latitude=-20.1,
                                   longitude=-44.9, accuracy_m=12)
        route_after_start = self.db.driver_trip(self.driver["driver_id"])
        detail_note = next(note for note in self.db.trip_detail(trip["trip_id"])["notes"]
                           if note["id"] == first_id)
        self.assertEqual(detail_note["active_start"]["latitude"], -20.1)
        self.assertEqual(detail_note["active_start"]["started_at"],
                         route_after_start["notes"][0]["start"]["started_at"])
        self.assertEqual(route_after_start["notes"][0]["start"]["id"], start_id)

    def test_detalhe_exibe_historico_cronologico_por_nota(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Carga com ocorrência")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        first_start = self.start_note(note_id, "historico-01", latitude=-20.0, longitude=-44.0, accuracy_m=15)
        result = self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-historico-01", "occurrence", "Falta de produto",
            start_id=first_start, completed_at=datetime.now(timezone.utc).isoformat(), occurrence_type="general",
            observation="Caixa avariada",
        )
        self.db.reopen_note(note_id, "Correção da tratativa", actor="admin:1")
        second_start = self.start_note(note_id, "historico-02")
        self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-historico-02", "occurrence", "",
            start_id=second_start, completed_at=datetime.now(timezone.utc).isoformat(),
            occurrence_type="partial_shortage", affected_item="1940",
            expected_quantity="12", received_quantity="11",
        )

        detail = self.db.trip_detail(trip["trip_id"])["notes"][0]
        events = detail["events"]
        self.assertEqual([event["status"] for event in events], ["occurrence", "occurrence"])
        self.assertEqual(events[0]["reason"], "Falta de produto")
        self.assertEqual(events[0]["driver"], "Motorista Teste")
        self.assertFalse(events[0]["has_evidence"])
        self.assertEqual(events[0]["observation"], "Caixa avariada")
        self.assertEqual(len(detail["reversals"]), 1)
        self.assertNotIn("note_id", events[0])
        self.assertIn("Cliente: Cliente Teste", result["share_text"])
        self.assertIn("NF: 123", result["share_text"])
        self.assertIn("Cidade: Divinópolis", result["share_text"])
        self.assertIn("Chegada no cliente:", result["share_text"])
        self.assertIn("Observação: Caixa avariada", result["share_text"])
        self.assertIn("Vendedor: Ian Silva", result["share_text"])
        self.assertIn("Motorista: Motorista Teste", result["share_text"])
        self.assertIn("Placa: ABC-1D23", result["share_text"])

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
        start_id = self.start_note(note_id, "idempotente")
        first = self.db.record_delivery(
            self.driver["driver_id"], note_id, event, "occurrence", "Produto avariado",
            start_id=start_id, completed_at=datetime.now(timezone.utc).isoformat(), occurrence_type="general",
        )
        second = self.db.record_delivery(self.driver["driver_id"], note_id, event, "occurrence", "Produto avariado")
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["share_text"], first["share_text"])
        self.assertEqual(second["status"], "occurrence")
        self.assertEqual(self.db.driver_trip(self.driver["driver_id"])["notes"], [])
        self.db.set_driver_password(self.driver["driver_id"], "OutraSenhaSegura2026!")
        self.assertIsNone(self.db.driver_by_token(token))

    def test_baixa_nao_pode_ser_sobrescrita_e_recusa_tem_motivos_fechados(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Regras de integridade")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("7" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        start_id = self.start_note(note_id, "recusa-regra")
        with self.assertRaisesRegex(BusinessError, "Pedido Duplicado ou Sem Pedido"):
            self.db.record_delivery(
                self.driver["driver_id"], note_id, "evento-recusa-invalida", "refused", "Cliente ausente",
                start_id=start_id, completed_at=datetime.now(timezone.utc).isoformat(),
            )
        self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-recusa-valida", "refused", "Sem Pedido",
            start_id=start_id, completed_at=datetime.now(timezone.utc).isoformat(),
        )
        with self.assertRaisesRegex(BusinessError, "somente um administrador pode estornar"):
            self.db.record_delivery(
                self.driver["driver_id"], note_id, "evento-sobrescrita", "occurrence", "Tentativa indevida",
                arrival_time="11:00", occurrence_type="general",
            )

    def test_reenvios_concorrentes_gravam_um_unico_evento(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Rota concorrente")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("8" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        start_id = self.start_note(note_id, "concorrente")
        barrier = threading.Barrier(2)
        results, errors = [], []

        def submit():
            try:
                barrier.wait(timeout=5)
                results.append(self.db.record_delivery(
                    self.driver["driver_id"], note_id, "evento-concorrente-01",
                    "occurrence", "Falha de internet simulada", start_id=start_id,
                    completed_at=datetime.now(timezone.utc).isoformat(), occurrence_type="general",
                ))
            except Exception as exc:  # registrado para a asserção da thread principal
                errors.append(exc)

        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(sorted(result["duplicate"] for result in results), [False, True])
        with self.db.connection() as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM delivery_events WHERE id='evento-concorrente-01'"
            ).fetchone()[0]
        self.assertEqual(total, 1)

    def test_motorista_pode_encerrar_a_propria_sessao(self):
        self.db.set_driver_password(self.driver["driver_id"], "SenhaIndividualSegura2026!")
        token = self.db.authenticate_driver("ABC1D23", "SenhaIndividualSegura2026!")
        self.assertEqual(self.db.driver_by_token(token), self.driver["driver_id"])

        self.db.revoke_driver_session(token)

        self.assertIsNone(self.db.driver_by_token(token))

    def test_sessoes_expiradas_sao_recusadas_e_removidas_pela_manutencao(self):
        self.db.set_driver_password(self.driver["driver_id"], "SenhaIndividualSegura2026!")
        driver_token = self.db.authenticate_driver("ABC1D23", "SenhaIndividualSegura2026!")
        admin = self.db.create_admin("gestor-expirado", "SenhaAdministrativaSegura!")
        admin_token = self.db.authenticate_admin("gestor-expirado", "SenhaAdministrativaSegura!")
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with self.db.connection() as connection:
            connection.execute("UPDATE driver_sessions SET expires_at=?", (past,))
            connection.execute("UPDATE admin_sessions SET expires_at=?", (past,))

        self.assertIsNone(self.db.driver_by_token(driver_token))
        self.assertIsNone(self.db.admin_identity_by_token(admin_token))
        result = self.db.cleanup_retention(
            now_at=datetime.now(timezone.utc),
            evidence_before=datetime.now(timezone.utc) - timedelta(days=180),
            audit_before=datetime.now(timezone.utc) - timedelta(days=730),
            apply=True,
        )
        self.assertEqual(result["deleted"]["driver_sessions"], 1)
        self.assertEqual(result["deleted"]["admin_sessions"], 1)
        self.assertEqual(admin["role"], "admin")

    def test_administrador_individual_revoga_sessao_e_redefine_senha(self):
        created = self.db.create_admin("Gestor.Teste", "SenhaAdministrativaSegura!")
        self.assertEqual(created["username"], "gestor.teste")
        self.assertEqual(created["role"], "admin")
        with self.assertRaisesRegex(BusinessError, "Credenciais inválidas"):
            self.db.authenticate_admin("gestor.teste", "incorreta")

        token = self.db.authenticate_admin("gestor.teste", "SenhaAdministrativaSegura!")
        self.assertEqual(self.db.admin_by_token(token), created["admin_id"])
        self.db.revoke_admin_session(token)
        self.assertIsNone(self.db.admin_by_token(token))

        old_token = self.db.authenticate_admin("gestor.teste", "SenhaAdministrativaSegura!")
        reset = self.db.create_admin("gestor.teste", "NovaSenhaAdministrativaSegura!")
        self.assertEqual(reset["admin_id"], created["admin_id"])
        self.assertIsNone(self.db.admin_by_token(old_token))

    def test_perfis_administrativos_e_protecao_do_ultimo_admin(self):
        admin = self.db.create_admin("gestor", "SenhaAdministrativaSegura!", "admin")
        operator = self.db.create_admin(
            "operador", "SenhaOperadorSegura2026!", "operator", actor=f"admin:{admin['admin_id']}",
        )
        auditor = self.db.create_admin(
            "auditor", "SenhaAuditorSegura2026!", "auditor", actor=f"admin:{admin['admin_id']}",
        )

        token = self.db.authenticate_admin("operador", "SenhaOperadorSegura2026!")
        self.assertEqual(self.db.admin_identity_by_token(token)["role"], "operator")
        self.assertEqual({user["role"] for user in self.db.admin_users()}, {"admin", "operator", "auditor"})
        updated = self.db.set_admin_access(
            operator["admin_id"], "auditor", False, actor=f"admin:{admin['admin_id']}",
            current_admin_id=admin["admin_id"],
        )
        self.assertFalse(updated["active"])
        self.assertIsNone(self.db.admin_identity_by_token(token))
        with self.assertRaisesRegex(BusinessError, "próprio acesso"):
            self.db.set_admin_access(
                admin["admin_id"], "operator", True, actor=f"admin:{admin['admin_id']}",
                current_admin_id=admin["admin_id"],
            )

    def test_limitador_de_login_persiste_apenas_hashes(self):
        client, plate = "192.0.2.10", "ABC1D23"
        for _ in range(5):
            self.db.record_login_failure(client, plate)

        reopened = Store(self.db.filename)
        self.assertTrue(reopened.login_blocked(client, plate))
        with reopened.connection() as connection:
            keys = [row[0] for row in connection.execute("SELECT key_hash FROM login_limits")]
        self.assertEqual(len(keys), 2)
        self.assertTrue(all(len(key) == 64 for key in keys))
        self.assertNotIn(client, keys)
        self.assertNotIn(plate, keys)

        reopened.clear_login_failures(client, plate)
        self.assertFalse(reopened.login_blocked(client, plate))

    def test_nao_permite_baixa_entregue_sem_foto(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        with self.assertRaises(BusinessError):
            self.db.record_delivery(self.driver["driver_id"], note_id, "evento-1234567890", "delivered", "")

    def test_rejeita_fotografias_truncadas_mesmo_com_cabecalho_valido(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Rota foto corrompida")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("7" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        corrupt = (
            b"\xff\xd8\xff" + b"sem-rodape" * 20,
            b"\x89PNG\r\n\x1a\n" + b"sem-iend" * 20,
        )
        for index, content in enumerate(corrupt):
            with self.subTest(index=index), self.assertRaisesRegex(BusinessError, "Fotografia inválida"):
                self.db.record_delivery(
                    self.driver["driver_id"], note_id, f"evento-corrompido-{index:02d}",
                    "delivered", "", base64.b64encode(content).decode(),
                )

    def test_foto_obrigatoria_e_encerramento(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Primeira carga")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("1" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        with self.assertRaises(BusinessError):
            self.db.close_driver_trip(self.driver["driver_id"], trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"test-fixture" * 15 + b"\xff\xd9").decode()
        start_id = self.start_note(note_id, "entregue")
        result = self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-1234567890", "delivered", "", jpeg,
            start_id=start_id, completed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.assertEqual(result["status"], "delivered")
        repeated = self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-1234567890", "delivered", ""
        )
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["status"], "delivered")
        with self.db.connection() as connection:
            evidence = connection.execute("SELECT sha256,content FROM evidence WHERE note_id=?", (note_id,)).fetchone()
            self.assertIsNotNone(evidence)
            self.assertTrue(evidence["content"].startswith(b"\xff\xd8\xff"))
        access = self.db.evidence_access("evento-1234567890")
        self.assertTrue(access["data_url"].startswith("data:image/jpeg;base64,"))
        self.assertIsNone(self.db.evidence_access("evento-inexistente"))
        closed = self.db.close_driver_trip(self.driver["driver_id"], trip["trip_id"])
        self.assertEqual(closed["status"], "closed")
        self.assertFalse(closed["duplicate"])
        self.assertTrue(self.db.close_driver_trip(self.driver["driver_id"], trip["trip_id"])["duplicate"])
        self.assertIsNone(self.db.driver_trip(self.driver["driver_id"]))

    def test_retencao_exportacoes_e_diagnostico_nao_removem_viagem_ativa(self):
        trip = self.db.create_trip(self.driver["vehicle_id"], "Rota retenção")
        self.db.import_notes(trip["trip_id"], [parse_nfe(xml("6" * 44), REGEX)])
        self.db.release_trip(trip["trip_id"])
        note_id = self.db.driver_trip(self.driver["driver_id"])["notes"][0]["id"]
        start_id = self.start_note(note_id, "retencao")
        self.db.record_delivery(
            self.driver["driver_id"], note_id, "evento-retencao-01", "occurrence", "Avaria no produto",
            start_id=start_id, completed_at=datetime.now(timezone.utc).isoformat(), occurrence_type="general",
        )
        old = (datetime.now(timezone.utc) - timedelta(days=900)).isoformat()
        with self.db.connection() as connection:
            connection.execute("UPDATE delivery_events SET created_at=?", (old,))
            connection.execute("UPDATE audit SET happened_at=?", (old,))

        preview = self.db.cleanup_retention(
            now_at=datetime.now(timezone.utc),
            evidence_before=datetime.now(timezone.utc) - timedelta(days=180),
            audit_before=datetime.now(timezone.utc) - timedelta(days=730),
            apply=False,
        )
        self.assertGreaterEqual(preview["candidates"]["audit"], 1)
        self.assertEqual(preview["candidates"]["evidence"], 0)
        self.assertEqual(self.db.occurrence_export()[0]["invoice"], "123")
        self.assertGreaterEqual(len(self.db.audit_export()), 1)
        diagnostic = self.db.diagnostics()
        self.assertEqual(diagnostic["active_trips"], 1)
        self.assertEqual(diagnostic["orphan_evidence"], 0)


if __name__ == "__main__":
    unittest.main()
