"""Persistência PostgreSQL para o piloto hospedado (schema privado app).

Não executa migrações nem cria credenciais automaticamente. Todas as operações
utilizam transações curtas e parâmetros, sem acesso direto do navegador ao banco.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from conrado.store import BusinessError, normalize_plate
from conrado.supabase_storage import StorageError, detect_image


class PostgresStore:
    def __init__(self, dsn: str, storage):
        if not dsn or storage is None:
            raise ValueError("Banco PostgreSQL e armazenamento privado são obrigatórios")
        self.dsn = dsn
        self.storage = storage

    @contextmanager
    def connection(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Instale psycopg[binary] para utilizar PostgreSQL") from exc
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10) as db:
            yield db

    @staticmethod
    def _audit(db, actor, event, kind, object_id, payload):
        db.execute("INSERT INTO app.audit(actor,event,object_type,object_id,payload) VALUES (%s,%s,%s,%s,%s::jsonb)",
                   (actor, event, kind, object_id, json.dumps(payload, ensure_ascii=False)))

    def create_driver(self, name, plate, vehicle_type):
        name, plate, vehicle_type = name.strip(), normalize_plate(plate), vehicle_type.strip()
        if not 2 <= len(name) <= 100 or not 1 <= len(vehicle_type) <= 30:
            raise BusinessError("Informe nome e tipo de veículo válidos")
        with self.connection() as db:
            if db.execute("SELECT 1 FROM app.vehicles WHERE plate=%s", (plate,)).fetchone():
                raise BusinessError("Placa já cadastrada")
            driver_id = db.execute("INSERT INTO app.drivers(name) VALUES (%s) RETURNING id", (name,)).fetchone()["id"]
            vehicle_id = db.execute("INSERT INTO app.vehicles(plate,vehicle_type,driver_id) VALUES (%s,%s,%s) RETURNING id",
                                    (plate, vehicle_type, driver_id)).fetchone()["id"]
            self._audit(db, "admin", "created", "driver", driver_id, {"plate": plate})
            return {"driver_id": driver_id, "vehicle_id": vehicle_id, "name": name, "plate": plate}

    def drivers(self):
        with self.connection() as db:
            rows = db.execute("""SELECT d.id AS driver_id,d.name,v.id AS vehicle_id,v.plate,v.vehicle_type,d.active
                FROM app.drivers d JOIN app.vehicles v ON v.driver_id=d.id ORDER BY d.name""").fetchall()
            return [dict(row) for row in rows]

    def create_trip(self, vehicle_id, label):
        label = label.strip()
        if not 2 <= len(label) <= 120:
            raise BusinessError("Informe uma identificação para a viagem")
        with self.connection() as db:
            vehicle = db.execute("SELECT driver_id,active FROM app.vehicles WHERE id=%s", (vehicle_id,)).fetchone()
            if not vehicle or not vehicle["active"]:
                raise BusinessError("Veículo inexistente ou inativo")
            trip_id = db.execute("INSERT INTO app.trips(vehicle_id,driver_id,label) VALUES (%s,%s,%s) RETURNING id",
                                 (vehicle_id, vehicle["driver_id"], label)).fetchone()["id"]
            self._audit(db, "admin", "created", "trip", trip_id, {"label": label})
            return {"trip_id": trip_id, "status": "draft"}

    def trips(self):
        with self.connection() as db:
            rows = db.execute("""SELECT t.id,t.label,t.status,v.plate,d.name AS driver,COUNT(n.id) AS notes
                FROM app.trips t JOIN app.vehicles v ON v.id=t.vehicle_id
                JOIN app.drivers d ON d.id=t.driver_id LEFT JOIN app.notes n ON n.trip_id=t.id
                GROUP BY t.id,v.plate,d.name ORDER BY t.id DESC""").fetchall()
            return [dict(row) for row in rows]

    def import_notes(self, trip_id, notes):
        if not 1 <= len(notes) <= 300:
            raise BusinessError("O lote deve conter de 1 a 300 notas")
        keys = [n["chave"] for n in notes]
        if len(set(keys)) != len(keys):
            raise BusinessError("Nota duplicada dentro do lote")
        with self.connection() as db:
            trip = db.execute("""SELECT t.status,v.plate FROM app.trips t JOIN app.vehicles v
                ON v.id=t.vehicle_id WHERE t.id=%s FOR UPDATE OF t""", (trip_id,)).fetchone()
            if not trip or trip["status"] != "draft":
                raise BusinessError("Viagem inexistente ou fora do estado de rascunho")
            for note in notes:
                if normalize_plate(note["placa"]) != trip["plate"]:
                    raise BusinessError("Placa da nota não corresponde ao veículo da viagem")
                exists = db.execute("""SELECT 1 FROM app.notes n JOIN app.trips t ON t.id=n.trip_id
                    WHERE n.access_key=%s AND t.status != 'closed'""", (note["chave"],)).fetchone()
                if exists:
                    raise BusinessError("NF-e já vinculada a uma viagem ativa")
            for n in notes:
                db.execute("""INSERT INTO app.notes(trip_id,access_key,number,recipient_id,recipient,address,gross_weight,volumes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (trip_id, n["chave"], n["numero"], n["destinatario_id"],
                    n["destinatario"], n["endereco"], n["peso_bruto_kg"], n["volumes"]))
            self._audit(db, "admin", "imported", "trip", trip_id, {"count": len(notes)})
            return {"trip_id": trip_id, "imported": len(notes)}

    def release_trip(self, trip_id):
        with self.connection() as db:
            trip = db.execute("SELECT vehicle_id,status FROM app.trips WHERE id=%s FOR UPDATE", (trip_id,)).fetchone()
            if not trip or trip["status"] != "draft":
                raise BusinessError("Somente uma viagem em rascunho pode ser liberada")
            if not db.execute("SELECT 1 FROM app.notes WHERE trip_id=%s", (trip_id,)).fetchone():
                raise BusinessError("Não é possível liberar viagem sem notas")
            if db.execute("SELECT 1 FROM app.trips WHERE vehicle_id=%s AND status='released'", (trip["vehicle_id"],)).fetchone():
                raise BusinessError("Já existe viagem liberada para este veículo")
            db.execute("UPDATE app.trips SET status='released',released_at=now() WHERE id=%s", (trip_id,))
            self._audit(db, "admin", "released", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "released"}

    def set_driver_password(self, driver_id, password):
        if len(password) < 12:
            raise BusinessError("A senha precisa ter pelo menos 12 caracteres")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
        with self.connection() as db:
            if not db.execute("SELECT 1 FROM app.drivers WHERE id=%s AND active=true", (driver_id,)).fetchone():
                raise BusinessError("Motorista não encontrado")
            db.execute("""INSERT INTO app.driver_auth(driver_id,salt,password_hash) VALUES (%s,%s,%s)
                ON CONFLICT(driver_id) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash""",
                (driver_id, salt.hex(), digest.hex()))
            db.execute("DELETE FROM app.driver_sessions WHERE driver_id=%s", (driver_id,))
            self._audit(db, "admin", "password_reset", "driver", driver_id, {})

    def authenticate_driver(self, plate, password):
        plate = normalize_plate(plate)
        with self.connection() as db:
            row = db.execute("""SELECT d.id,a.salt,a.password_hash FROM app.drivers d
                JOIN app.vehicles v ON v.driver_id=d.id JOIN app.driver_auth a ON a.driver_id=d.id
                WHERE v.plate=%s AND d.active=true AND v.active=true""", (plate,)).fetchone()
            if not row:
                raise BusinessError("Credenciais inválidas")
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), 260_000)
            if not hmac.compare_digest(digest.hex(), row["password_hash"]):
                raise BusinessError("Credenciais inválidas")
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO app.driver_sessions(token_hash,driver_id,expires_at) VALUES (%s,%s,%s)",
                       (hashlib.sha256(token.encode()).hexdigest(), row["id"], datetime.now(timezone.utc) + timedelta(hours=12)))
            return token

    def driver_by_token(self, token):
        if not token:
            return None
        with self.connection() as db:
            row = db.execute("SELECT driver_id FROM app.driver_sessions WHERE token_hash=%s AND expires_at>now()",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return int(row["driver_id"]) if row else None

    def driver_trip(self, driver_id):
        with self.connection() as db:
            trip = db.execute("""SELECT t.id,t.label,v.plate FROM app.trips t JOIN app.vehicles v
                ON v.id=t.vehicle_id WHERE t.driver_id=%s AND t.status='released' ORDER BY t.id LIMIT 1""",
                (driver_id,)).fetchone()
            if not trip:
                return None
            notes = db.execute("""SELECT id,number,recipient,recipient_id,address,status FROM app.notes
                WHERE trip_id=%s ORDER BY recipient,address,number""", (trip["id"],)).fetchall()
            return {**dict(trip), "notes": [dict(n) for n in notes]}

    def record_delivery(self, driver_id, note_id, event_id, status, reason, photo_base64=None):
        if status not in ("delivered", "partial", "refused", "occurrence"):
            raise BusinessError("Situação inválida")
        if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise BusinessError("Identificador da operação inválido")
        if len(reason) > 1000 or (status != "delivered" and len(reason.strip()) < 3):
            raise BusinessError("Informe um motivo válido para a ocorrência")
        photo = None
        if photo_base64:
            try:
                photo = base64.b64decode(photo_base64, validate=True)
                detect_image(photo)
            except (ValueError, binascii.Error, StorageError) as exc:
                raise BusinessError("Fotografia inválida; envie JPEG ou PNG até 5 MB") from exc
        if status == "delivered" and photo is None:
            raise BusinessError("Fotografia do canhoto é obrigatória para marcar entrega concluída")
        with self.connection() as db:
            existing = db.execute("SELECT note_id,driver_id FROM app.delivery_events WHERE id=%s", (event_id,)).fetchone()
            if existing:
                if existing["note_id"] != note_id or existing["driver_id"] != driver_id:
                    raise BusinessError("Identificador de operação já utilizado")
                return {"event_id": event_id, "duplicate": True}
            note = db.execute("""SELECT n.status,t.id AS trip_id,t.status AS trip_status FROM app.notes n
                JOIN app.trips t ON t.id=n.trip_id WHERE n.id=%s AND t.driver_id=%s FOR UPDATE OF n""",
                (note_id, driver_id)).fetchone()
            if not note or note["trip_status"] != "released":
                raise BusinessError("Nota não pertence à viagem ativa deste motorista")
            db.execute("""INSERT INTO app.delivery_events(id,note_id,driver_id,status,reason)
                VALUES (%s,%s,%s,%s,%s)""", (event_id, note_id, driver_id, status, reason.strip()))
            if photo is not None:
                # Upload privado; se o commit do banco falhar, o objeto órfão será
                # tratado futuramente por rotina de limpeza idempotente.
                try:
                    uploaded = self.storage.upload(note["trip_id"], note_id, event_id, photo)
                except StorageError as exc:
                    raise BusinessError("Não foi possível salvar a foto; tente novamente") from exc
                db.execute("""INSERT INTO app.evidence(note_id,event_id,storage_path,mime,sha256)
                    VALUES (%s,%s,%s,%s,%s)""", (note_id, event_id, uploaded.path, uploaded.mime, uploaded.sha256))
            db.execute("UPDATE app.notes SET status=%s WHERE id=%s", (status, note_id))
            self._audit(db, f"driver:{driver_id}", "status_changed", "note", note_id,
                        {"from": note["status"], "to": status, "reason": reason.strip(), "event_id": event_id})
            return {"event_id": event_id, "duplicate": False, "status": status}

    def close_driver_trip(self, driver_id, trip_id):
        with self.connection() as db:
            trip = db.execute("SELECT status FROM app.trips WHERE id=%s AND driver_id=%s FOR UPDATE",
                              (trip_id, driver_id)).fetchone()
            if not trip or trip["status"] != "released":
                raise BusinessError("Viagem não está ativa para este motorista")
            pending = db.execute("SELECT COUNT(*) AS total FROM app.notes WHERE trip_id=%s AND status='pending'",
                                 (trip_id,)).fetchone()["total"]
            if pending:
                raise BusinessError(f"Existem {pending} NF-e sem baixa ou ocorrência")
            db.execute("UPDATE app.trips SET status='closed',closed_at=now() WHERE id=%s", (trip_id,))
            self._audit(db, f"driver:{driver_id}", "closed", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "closed"}
