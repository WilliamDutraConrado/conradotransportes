"""Persistência local do piloto: motoristas, veículos, viagens e notas."""

from __future__ import annotations

import json
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
