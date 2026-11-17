"""Persistência local do piloto: motoristas, veículos, viagens e notas."""

from __future__ import annotations

import json
import hashlib
import base64
import binascii
import hmac
import secrets
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class BusinessError(ValueError):
    """Erro de validação ou regra operacional."""


def normalize_plate(plate: str) -> str:
    result = str(plate).strip().upper().replace("-", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}", result):
        raise BusinessError("Placa inválida")
    return result


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, filename: str | Path):
        self.filename = str(filename)
        self.init()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.filename, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self):
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY, plate TEXT NOT NULL UNIQUE,
                vehicle_type TEXT NOT NULL, driver_id INTEGER NOT NULL REFERENCES drivers(id),
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trips (
                id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
                driver_id INTEGER NOT NULL REFERENCES drivers(id),
                label TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft'
                    CHECK(status IN ('draft','released','closed')),
                created_at TEXT NOT NULL, released_at TEXT, closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY, trip_id INTEGER NOT NULL REFERENCES trips(id),
                access_key TEXT NOT NULL, number TEXT NOT NULL,
                recipient_id TEXT NOT NULL, recipient TEXT NOT NULL, address TEXT NOT NULL,
                gross_weight REAL NOT NULL, volumes INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','delivered','partial','refused','occurrence')),
                UNIQUE(trip_id, access_key)
            );
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY, happened_at TEXT NOT NULL, actor TEXT NOT NULL,
                event TEXT NOT NULL, object_type TEXT NOT NULL, object_id INTEGER NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS driver_auth (
                driver_id INTEGER PRIMARY KEY REFERENCES drivers(id),
                salt TEXT NOT NULL, password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS driver_sessions (
                token_hash TEXT PRIMARY KEY, driver_id INTEGER NOT NULL REFERENCES drivers(id),
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_events (
                id TEXT PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id),
                driver_id INTEGER NOT NULL REFERENCES drivers(id),
                status TEXT NOT NULL CHECK(status IN ('delivered','partial','refused','occurrence')),
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id),
                event_id TEXT NOT NULL REFERENCES delivery_events(id),
                mime TEXT NOT NULL, sha256 TEXT NOT NULL,
                content BLOB NOT NULL, created_at TEXT NOT NULL
            );
            """)

    @staticmethod
    def _audit(db, actor: str, event: str, kind: str, object_id: int, payload: dict):
        db.execute("INSERT INTO audit(happened_at, actor, event, object_type, object_id, payload) VALUES(?,?,?,?,?,?)",
                   (now(), actor, event, kind, object_id, json.dumps(payload, ensure_ascii=False)))

    def create_driver(self, name: str, plate: str, vehicle_type: str) -> dict:
        name = name.strip()
        if not (2 <= len(name) <= 100):
            raise BusinessError("Informe o nome do motorista")
        plate = normalize_plate(plate)
        vehicle_type = vehicle_type.strip()
        if not (1 <= len(vehicle_type) <= 30):
            raise BusinessError("Informe o tipo de veículo")
        try:
            with self.connection() as db:
                driver = db.execute("INSERT INTO drivers(name,created_at) VALUES(?,?)", (name, now()))
                vehicle = db.execute("INSERT INTO vehicles(plate,vehicle_type,driver_id,created_at) VALUES(?,?,?,?)",
                                     (plate, vehicle_type, driver.lastrowid, now()))
                self._audit(db, "admin", "created", "driver", driver.lastrowid, {"plate": plate})
                return {"driver_id": driver.lastrowid, "vehicle_id": vehicle.lastrowid, "name": name, "plate": plate}
        except sqlite3.IntegrityError as exc:
            raise BusinessError("Placa já cadastrada") from exc

    def drivers(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""SELECT d.id AS driver_id,d.name,v.id AS vehicle_id,v.plate,v.vehicle_type,
                                d.active FROM drivers d JOIN vehicles v ON v.driver_id=d.id ORDER BY d.name""")
            return [dict(row) for row in rows]

    def create_trip(self, vehicle_id: int, label: str) -> dict:
        label = label.strip()
        if not (2 <= len(label) <= 120):
            raise BusinessError("Informe uma identificação para a viagem")
        with self.connection() as db:
            vehicle = db.execute("SELECT id,driver_id,active FROM vehicles WHERE id=?", (vehicle_id,)).fetchone()
            if not vehicle or not vehicle["active"]:
                raise BusinessError("Veículo inexistente ou inativo")
            # Um veículo só pode ter uma viagem liberada por vez.
            trip = db.execute("INSERT INTO trips(vehicle_id,driver_id,label,created_at) VALUES(?,?,?,?)",
                              (vehicle_id, vehicle["driver_id"], label, now()))
            self._audit(db, "admin", "created", "trip", trip.lastrowid, {"label": label})
            return {"trip_id": trip.lastrowid, "status": "draft"}

    def trips(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""SELECT t.id,t.label,t.status,v.plate,d.name AS driver,
                                COUNT(n.id) AS notes FROM trips t
                                JOIN vehicles v ON v.id=t.vehicle_id
                                JOIN drivers d ON d.id=t.driver_id
                                LEFT JOIN notes n ON n.trip_id=t.id
                                GROUP BY t.id ORDER BY t.id DESC""")
            return [dict(row) for row in rows]

    def summary(self) -> dict:
        """Indicadores da operação a partir das NF-e efetivamente importadas."""
        with self.connection() as db:
            trips = {row["status"]: row["total"] for row in db.execute(
                "SELECT status,COUNT(*) AS total FROM trips GROUP BY status")}
            notes = {row["status"]: row["total"] for row in db.execute(
                "SELECT status,COUNT(*) AS total FROM notes GROUP BY status")}
            return {
                "trips": {key: trips.get(key, 0) for key in ("draft", "released", "closed")},
                "notes": {key: notes.get(key, 0) for key in ("pending", "delivered", "partial", "refused", "occurrence")},
            }

    def trip_detail(self, trip_id: int) -> dict | None:
        """Conferência administrativa de NF-e individuais, inclusive no mesmo destino."""
        with self.connection() as db:
            trip = db.execute("""SELECT t.id,t.label,t.status,v.plate,d.name AS driver
                FROM trips t JOIN vehicles v ON v.id=t.vehicle_id
                JOIN drivers d ON d.id=t.driver_id WHERE t.id=?""", (trip_id,)).fetchone()
            if not trip:
                return None
            notes = db.execute("""SELECT n.id,n.number,n.recipient,n.address,n.status,
                n.gross_weight,n.volumes FROM notes n WHERE n.trip_id=?
                ORDER BY n.recipient,n.address,n.number,n.id""", (trip_id,)).fetchall()
            return {**dict(trip), "notes": [dict(n) for n in notes]}

    def import_notes(self, trip_id: int, notes: list[dict]) -> dict:
        if not notes or len(notes) > 300:
            raise BusinessError("O lote deve conter de 1 a 300 notas")
        with self.connection() as db:
            trip = db.execute("""SELECT t.status,v.plate FROM trips t JOIN vehicles v ON v.id=t.vehicle_id
                                 WHERE t.id=?""", (trip_id,)).fetchone()
            if not trip or trip["status"] != "draft":
                raise BusinessError("Viagem inexistente ou fora do estado de rascunho")
            keys = [n["chave"] for n in notes]
            if len(set(keys)) != len(keys):
                raise BusinessError("Nota duplicada dentro do lote")
            for note in notes:
                if note["placa"] != trip["plate"]:
                    raise BusinessError("Placa da nota não corresponde ao veículo da viagem")
                elsewhere = db.execute("""SELECT 1 FROM notes n JOIN trips t ON t.id=n.trip_id
                                        WHERE n.access_key=? AND t.status != 'closed'""", (note["chave"],)).fetchone()
                if elsewhere:
                    raise BusinessError("NF-e já vinculada a uma viagem ativa")
            for note in notes:
                db.execute("""INSERT INTO notes(trip_id,access_key,number,recipient_id,recipient,address,
                              gross_weight,volumes) VALUES(?,?,?,?,?,?,?,?)""",
                           (trip_id, note["chave"], note["numero"], note["destinatario_id"], note["destinatario"],
                            note["endereco"], note["peso_bruto_kg"], note["volumes"]))
            self._audit(db, "admin", "imported", "trip", trip_id, {"count": len(notes)})
            return {"trip_id": trip_id, "imported": len(notes)}

    def release_trip(self, trip_id: int) -> dict:
        with self.connection() as db:
            trip = db.execute("SELECT vehicle_id,status FROM trips WHERE id=?", (trip_id,)).fetchone()
            if not trip or trip["status"] != "draft":
                raise BusinessError("Somente uma viagem em rascunho pode ser liberada")
            if not db.execute("SELECT 1 FROM notes WHERE trip_id=?", (trip_id,)).fetchone():
                raise BusinessError("Não é possível liberar viagem sem notas")
            other = db.execute("SELECT 1 FROM trips WHERE vehicle_id=? AND status='released'", (trip["vehicle_id"],)).fetchone()
            if other:
                raise BusinessError("Já existe viagem liberada para este veículo")
            db.execute("UPDATE trips SET status='released', released_at=? WHERE id=?", (now(), trip_id))
            self._audit(db, "admin", "released", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "released"}

    def set_driver_password(self, driver_id: int, password: str) -> None:
        if len(password) < 12:
            raise BusinessError("A senha precisa ter pelo menos 12 caracteres")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
        with self.connection() as db:
            driver = db.execute("SELECT id FROM drivers WHERE id=? AND active=1", (driver_id,)).fetchone()
            if not driver:
                raise BusinessError("Motorista não encontrado")
            db.execute("""INSERT INTO driver_auth(driver_id,salt,password_hash) VALUES(?,?,?)
                       ON CONFLICT(driver_id) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash""",
                       (driver_id, salt.hex(), digest.hex()))
            db.execute("DELETE FROM driver_sessions WHERE driver_id=?", (driver_id,))
            self._audit(db, "admin", "password_reset", "driver", driver_id, {})

    def authenticate_driver(self, plate: str, password: str) -> str:
        plate = normalize_plate(plate)
        with self.connection() as db:
            row = db.execute("""SELECT d.id,a.salt,a.password_hash FROM drivers d
                JOIN vehicles v ON v.driver_id=d.id JOIN driver_auth a ON a.driver_id=d.id
                WHERE v.plate=? AND d.active=1 AND v.active=1""", (plate,)).fetchone()
            if not row:
                raise BusinessError("Credenciais inválidas")
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), 260_000)
            if not hmac.compare_digest(digest.hex(), row["password_hash"]):
                raise BusinessError("Credenciais inválidas")
            token = secrets.token_urlsafe(32)
            from datetime import timedelta
            expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            db.execute("INSERT INTO driver_sessions(token_hash,driver_id,expires_at) VALUES(?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), row["id"], expires))
            return token

    def driver_by_token(self, token: str) -> int | None:
        if not token:
            return None
        with self.connection() as db:
            row = db.execute("""SELECT driver_id FROM driver_sessions
                WHERE token_hash=? AND expires_at>?""",
                (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
            return int(row["driver_id"]) if row else None

    def driver_trip(self, driver_id: int) -> dict | None:
        with self.connection() as db:
            trip = db.execute("""SELECT t.id,t.label,v.plate FROM trips t
                JOIN vehicles v ON v.id=t.vehicle_id
                WHERE t.driver_id=? AND t.status='released' ORDER BY t.id LIMIT 1""", (driver_id,)).fetchone()
            if not trip:
                return None
            notes = db.execute("""SELECT id,number,recipient,recipient_id,address,status FROM notes
                WHERE trip_id=? ORDER BY recipient,address,number""", (trip["id"],)).fetchall()
            return {**dict(trip), "notes": [dict(n) for n in notes]}

    def record_delivery(self, driver_id: int, note_id: int, event_id: str, status: str, reason: str,
                        photo_base64: str | None = None) -> dict:
        if status not in ("delivered", "partial", "refused", "occurrence"):
            raise BusinessError("Situação inválida")
        if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise BusinessError("Identificador da operação inválido")
        if len(reason) > 1000:
            raise BusinessError("Observação muito longa")
        if status != "delivered" and len(reason.strip()) < 3:
            raise BusinessError("Informe o motivo da ocorrência")
        photo = None
        photo_type = None
        if photo_base64:
            try:
                photo = base64.b64decode(photo_base64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise BusinessError("Foto inválida") from exc
            if len(photo) > 5_000_000 or len(photo) < 100:
                raise BusinessError("Foto precisa ter entre 100 bytes e 5 MB")
            if photo.startswith(b"\xff\xd8\xff"):
                photo_type = "image/jpeg"
            elif photo.startswith(b"\x89PNG\r\n\x1a\n"):
                photo_type = "image/png"
            else:
                raise BusinessError("Foto deve ser JPEG ou PNG")
        if status == "delivered" and photo is None:
            raise BusinessError("Fotografia do canhoto é obrigatória para marcar entrega concluída")

        with self.connection() as db:
            existing = db.execute("SELECT id,note_id,driver_id FROM delivery_events WHERE id=?", (event_id,)).fetchone()
            if existing:
                if existing["driver_id"] != driver_id or existing["note_id"] != note_id:
                    raise BusinessError("Identificador de operação já utilizado")
                return {"event_id": event_id, "duplicate": True}
            note = db.execute("""SELECT n.id,n.status,t.status AS trip_status FROM notes n
                JOIN trips t ON t.id=n.trip_id WHERE n.id=? AND t.driver_id=?""",
                (note_id, driver_id)).fetchone()
            if not note or note["trip_status"] != "released":
                raise BusinessError("Nota não pertence à viagem ativa deste motorista")
            db.execute("""INSERT INTO delivery_events(id,note_id,driver_id,status,reason,created_at)
                VALUES(?,?,?,?,?,?)""", (event_id, note_id, driver_id, status, reason.strip(), now()))
            if photo is not None:
                db.execute("""INSERT INTO evidence(note_id,event_id,mime,sha256,content,created_at)
                    VALUES(?,?,?,?,?,?)""", (note_id, event_id, photo_type, hashlib.sha256(photo).hexdigest(), photo, now()))
            db.execute("UPDATE notes SET status=? WHERE id=?", (status, note_id))
            self._audit(db, f"driver:{driver_id}", "status_changed", "note", note_id,
                        {"from": note["status"], "to": status, "reason": reason.strip(), "event_id": event_id})
            return {"event_id": event_id, "duplicate": False, "status": status}

    def close_driver_trip(self, driver_id: int, trip_id: int) -> dict:
        with self.connection() as db:
            trip = db.execute("SELECT id,status FROM trips WHERE id=? AND driver_id=?", (trip_id, driver_id)).fetchone()
            if not trip or trip["status"] != "released":
                raise BusinessError("Viagem não está ativa para este motorista")
            pending = db.execute("SELECT COUNT(*) FROM notes WHERE trip_id=? AND status='pending'", (trip_id,)).fetchone()[0]
            if pending:
                raise BusinessError(f"Existem {pending} NF-e sem baixa ou ocorrência")
            db.execute("UPDATE trips SET status='closed',closed_at=? WHERE id=?", (now(), trip_id))
            self._audit(db, f"driver:{driver_id}", "closed", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "closed"}
