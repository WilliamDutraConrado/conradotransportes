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
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from conrado.supabase_storage import StorageError, detect_image


class BusinessError(ValueError):
    """Erro de validação ou regra operacional."""


class ServiceUnavailable(RuntimeError):
    """Falha transitória segura, elegível para a fila offline."""


def normalize_plate(plate: str) -> str:
    result = str(plate).strip().upper().replace("-", "").replace(" ", "")
    if not re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}", result):
        raise BusinessError("Placa inválida")
    return result


def normalize_admin_username(username: str) -> str:
    result = str(username).strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,50}", result):
        raise BusinessError("Usuário administrativo inválido")
    return result


WEAK_DRIVER_PINS = frozenset({
    "00000", "11111", "22222", "33333", "44444",
    "55555", "66666", "77777", "88888", "99999",
    "01234", "12345", "23456", "34567", "45678", "56789",
    "98765", "87654", "76543", "65432", "54321", "43210",
})


def normalize_driver_pin(value) -> str:
    pin = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{5}", pin):
        raise BusinessError("O PIN do motorista deve conter exatamente 5 dígitos")
    if pin in WEAK_DRIVER_PINS:
        raise BusinessError("Escolha um PIN menos previsível")
    return pin


ADMIN_ROLES = frozenset({"admin", "operator", "auditor"})
DELIVERY_STATUSES = frozenset({"delivered", "refused", "occurrence"})
REFUSAL_REASONS = frozenset({"Pedido Duplicado", "Sem Pedido"})
OCCURRENCE_TYPES = frozenset({"general", "partial_shortage"})


def normalize_admin_role(role: str) -> str:
    result = str(role).strip().lower()
    if result not in ADMIN_ROLES:
        raise BusinessError("Perfil administrativo inválido")
    return result


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def invoice_sort_key(value) -> tuple:
    """Ordena NF-e naturalmente, mantendo uma saída estável para valores atípicos."""
    text = str(value or "").strip()
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", text) if part
    ) or ((1, ""),)


def client_timestamp(value: str, label: str) -> datetime:
    """Valida horário informado pelo aparelho sem confundi-lo com o horário do servidor."""
    try:
        moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BusinessError(f"{label} inválido") from exc
    if moment.tzinfo is None:
        raise BusinessError(f"{label} precisa incluir o fuso horário")
    moment = moment.astimezone(timezone.utc)
    reference = datetime.now(timezone.utc)
    if moment > reference + timedelta(minutes=15) or moment < reference - timedelta(days=7):
        raise BusinessError(f"{label} está fora do intervalo permitido")
    return moment


def normalized_location(latitude, longitude, accuracy_m) -> tuple[float | None, float | None, float | None]:
    values = (latitude, longitude, accuracy_m)
    if all(value in (None, "") for value in values):
        return None, None, None
    if any(value in (None, "") for value in values):
        raise BusinessError("Localização incompleta")
    try:
        lat, lng, accuracy = map(float, values)
    except (TypeError, ValueError) as exc:
        raise BusinessError("Localização inválida") from exc
    if not -90 <= lat <= 90 or not -180 <= lng <= 180 or not 0 <= accuracy <= 10_000:
        raise BusinessError("Localização fora dos limites válidos")
    return round(lat, 6), round(lng, 6), round(accuracy, 1)


def normalized_quantity(value: str, label: str) -> Decimal:
    text = str(value or "").strip().replace(",", ".")
    try:
        quantity = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise BusinessError(f"{label} inválida") from exc
    if not quantity.is_finite() or quantity < 0 or quantity > Decimal("9999999999.9999"):
        raise BusinessError(f"{label} inválida")
    return quantity


def login_limit_keys(client: str, plate: str) -> tuple[str, str]:
    normalized_plate = str(plate).strip().upper().replace("-", "").replace(" ", "")
    return tuple(
        hashlib.sha256(value.encode()).hexdigest()
        for value in (f"ip:{client}", f"plate:{normalized_plate}")
    )


def occurrence_message(note: dict, status: str, reason: str, created_at, arrival_time: str,
                       affected_item: str, seller_name: str, observation: str = "") -> str | None:
    """Monta o texto operacional sem depender de preenchimento duplicado pelo motorista."""
    if status == "delivered":
        return None
    moment = created_at if isinstance(created_at, datetime) else datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        sao_paulo = ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        # Algumas instalações mínimas do Python no Windows não incluem a base
        # IANA. Desde 2019 São Paulo opera em UTC-3; o fallback evita bloquear
        # uma ocorrência sem transformar essa ausência local em dependência.
        sao_paulo = timezone(timedelta(hours=-3), "America/Sao_Paulo")
    local = moment.astimezone(sao_paulo)
    labels = {"partial": "NF Parcial", "refused": "Recusa", "occurrence": "Ocorrência"}
    plate = str(note["plate"])
    if re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}", plate):
        plate = f"{plate[:3]}-{plate[3:]}"
    lines = [
        "🚨🚚  ATENÇÃO 🚚🚨",
        "OCORRÊNCIA - CONRADO TRANSPORTES",
        f"Cliente: {note['recipient']}",
        f"NF: {note['number']}",
        f"Cidade: {note['city'] or 'Não informada no XML'}",
        f"Chegada no cliente: {arrival_time or local.strftime('%H:%M')}",
        f"Motivo: {labels.get(status, status)} - {reason.strip()}",
    ]
    if affected_item.strip():
        lines.append(f"Item: {affected_item.strip()}")
    if observation.strip():
        lines.append(f"Observação: {observation.strip()}")
    lines.extend([
        f"Vendedor: {seller_name.strip() or note['seller'] or 'Não informado no XML'}",
        f"Motorista: {note['driver']}",
        f"Placa: {plate}",
        f"Data: {local.strftime('%d/%m')}",
    ])
    return "\n".join(lines)


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
                city TEXT NOT NULL DEFAULT '', seller TEXT NOT NULL DEFAULT '',
                items_json TEXT NOT NULL DEFAULT '[]',
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
            CREATE TABLE IF NOT EXISTS admin_users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'operator'
                    CHECK(role IN ('admin','operator','auditor')),
                active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY,
                admin_id INTEGER NOT NULL REFERENCES admin_users(id),
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS driver_sessions (
                token_hash TEXT PRIMARY KEY, driver_id INTEGER NOT NULL REFERENCES drivers(id),
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_limits (
                key_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL,
                window_started REAL NOT NULL, blocked_until REAL
            );
            CREATE TABLE IF NOT EXISTS delivery_events (
                id TEXT PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id),
                driver_id INTEGER NOT NULL REFERENCES drivers(id),
                status TEXT NOT NULL CHECK(status IN ('delivered','partial','refused','occurrence')),
                reason TEXT NOT NULL DEFAULT '',
                arrival_time TEXT NOT NULL DEFAULT '', affected_item TEXT NOT NULL DEFAULT '',
                seller_name TEXT NOT NULL DEFAULT '',
                start_id TEXT REFERENCES delivery_starts(id), completed_at TEXT NOT NULL DEFAULT '',
                occurrence_type TEXT NOT NULL DEFAULT '', observation TEXT NOT NULL DEFAULT '',
                expected_quantity TEXT NOT NULL DEFAULT '', received_quantity TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_starts (
                id TEXT PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id),
                driver_id INTEGER NOT NULL REFERENCES drivers(id),
                started_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
                latitude REAL, longitude REAL, accuracy_m REAL,
                CHECK ((latitude IS NULL AND longitude IS NULL AND accuracy_m IS NULL) OR
                       (latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180 AND
                        accuracy_m BETWEEN 0 AND 10000))
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL REFERENCES notes(id),
                event_id TEXT NOT NULL REFERENCES delivery_events(id),
                mime TEXT NOT NULL, sha256 TEXT NOT NULL,
                content BLOB NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_by_happened_at ON audit(happened_at);
            CREATE INDEX IF NOT EXISTS evidence_by_created_at ON evidence(created_at);
            CREATE INDEX IF NOT EXISTS events_by_created_at ON delivery_events(created_at);
            CREATE INDEX IF NOT EXISTS starts_by_note ON delivery_starts(note_id, recorded_at);
            CREATE UNIQUE INDEX IF NOT EXISTS events_start_once ON delivery_events(start_id)
                WHERE start_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS admin_sessions_by_expiry ON admin_sessions(expires_at);
            CREATE INDEX IF NOT EXISTS driver_sessions_by_expiry ON driver_sessions(expires_at);
            """)
            # Migração local idempotente para bancos criados por versões anteriores.
            note_columns = {row["name"] for row in db.execute("PRAGMA table_info(notes)")}
            for name, definition in (
                ("city", "TEXT NOT NULL DEFAULT ''"),
                ("seller", "TEXT NOT NULL DEFAULT ''"),
                ("items_json", "TEXT NOT NULL DEFAULT '[]'"),
            ):
                if name not in note_columns:
                    db.execute(f"ALTER TABLE notes ADD COLUMN {name} {definition}")
            event_columns = {row["name"] for row in db.execute("PRAGMA table_info(delivery_events)")}
            for name, definition in (
                ("arrival_time", "TEXT NOT NULL DEFAULT ''"),
                ("affected_item", "TEXT NOT NULL DEFAULT ''"),
                ("seller_name", "TEXT NOT NULL DEFAULT ''"),
                ("start_id", "TEXT"),
                ("completed_at", "TEXT NOT NULL DEFAULT ''"),
                ("occurrence_type", "TEXT NOT NULL DEFAULT ''"),
                ("observation", "TEXT NOT NULL DEFAULT ''"),
                ("expected_quantity", "TEXT NOT NULL DEFAULT ''"),
                ("received_quantity", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in event_columns:
                    db.execute(f"ALTER TABLE delivery_events ADD COLUMN {name} {definition}")
            admin_columns = {row["name"] for row in db.execute("PRAGMA table_info(admin_users)")}
            if "role" not in admin_columns:
                # Contas locais anteriores eram, por definição, administradores completos.
                db.execute("ALTER TABLE admin_users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")

    def healthcheck(self) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1").fetchone()[0] == 1

    def diagnostics(self) -> dict:
        """Indicadores técnicos agregados, sem placas, clientes ou credenciais."""
        moment = now()
        with self.connection() as db:
            return {
                "database": "ok",
                "active_trips": db.execute("SELECT COUNT(*) FROM trips WHERE status='released'").fetchone()[0],
                "pending_notes": db.execute("SELECT COUNT(*) FROM notes WHERE status='pending'").fetchone()[0],
                "evidence": db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
                "expired_admin_sessions": db.execute(
                    "SELECT COUNT(*) FROM admin_sessions WHERE expires_at<=?", (moment,)
                ).fetchone()[0],
                "expired_driver_sessions": db.execute(
                    "SELECT COUNT(*) FROM driver_sessions WHERE expires_at<=?", (moment,)
                ).fetchone()[0],
                "orphan_evidence": db.execute("""SELECT COUNT(*) FROM evidence x
                    WHERE NOT EXISTS (SELECT 1 FROM delivery_events e WHERE e.id=x.event_id)""").fetchone()[0],
                "last_event_at": db.execute("SELECT MAX(created_at) FROM delivery_events").fetchone()[0],
                "last_audit_at": db.execute("SELECT MAX(happened_at) FROM audit").fetchone()[0],
            }

    def audit_export(self, limit: int = 50_000) -> list[dict]:
        if not 1 <= limit <= 50_000:
            raise BusinessError("Limite de exportação inválido")
        with self.connection() as db:
            rows = db.execute("""SELECT happened_at,actor,event,object_type,object_id,payload
                FROM audit ORDER BY happened_at,id LIMIT ?""", (limit,)).fetchall()
            return [dict(row) for row in rows]

    def occurrence_export(self, limit: int = 50_000) -> list[dict]:
        if not 1 <= limit <= 50_000:
            raise BusinessError("Limite de exportação inválido")
        with self.connection() as db:
            rows = db.execute("""SELECT e.created_at,e.completed_at,e.status,e.reason,e.arrival_time,
                e.affected_item,e.occurrence_type,e.observation,e.expected_quantity,e.received_quantity,
                e.seller_name,s.started_at,s.latitude,s.longitude,s.accuracy_m,
                n.number AS invoice,n.recipient,n.city,t.label AS trip,v.plate,
                d.name AS driver,CASE WHEN x.id IS NULL THEN 0 ELSE 1 END AS has_evidence
                FROM delivery_events e JOIN notes n ON n.id=e.note_id
                JOIN trips t ON t.id=n.trip_id JOIN vehicles v ON v.id=t.vehicle_id
                JOIN drivers d ON d.id=e.driver_id LEFT JOIN evidence x ON x.event_id=e.id
                LEFT JOIN delivery_starts s ON s.id=e.start_id
                WHERE e.status<>'delivered' ORDER BY e.created_at,e.id LIMIT ?""", (limit,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["duration_seconds"] = ""
                if item.get("started_at") and item.get("completed_at"):
                    try:
                        started = datetime.fromisoformat(item["started_at"].replace("Z", "+00:00"))
                        completed = datetime.fromisoformat(item["completed_at"].replace("Z", "+00:00"))
                        item["duration_seconds"] = max(0, int((completed - started).total_seconds()))
                    except ValueError:
                        pass
                result.append(item)
            return result

    def cleanup_retention(self, *, now_at: datetime, evidence_before: datetime,
                          audit_before: datetime, apply: bool = False) -> dict:
        """Limpa somente sessões vencidas e dados além da retenção aprovada.

        Canhotos só são elegíveis quando a viagem já foi encerrada.
        """
        moment = now_at.astimezone(timezone.utc)
        moment_iso = moment.isoformat()
        evidence_iso = evidence_before.astimezone(timezone.utc).isoformat()
        audit_iso = audit_before.astimezone(timezone.utc).isoformat()
        login_cutoff = moment.timestamp() - 86_400
        with self.connection() as db:
            counts = {
                "admin_sessions": db.execute(
                    "SELECT COUNT(*) FROM admin_sessions WHERE expires_at<=?", (moment_iso,)
                ).fetchone()[0],
                "driver_sessions": db.execute(
                    "SELECT COUNT(*) FROM driver_sessions WHERE expires_at<=?", (moment_iso,)
                ).fetchone()[0],
                "login_limits": db.execute(
                    "SELECT COUNT(*) FROM login_limits WHERE window_started<?", (login_cutoff,)
                ).fetchone()[0],
                "evidence": db.execute("""SELECT COUNT(*) FROM evidence x
                    JOIN notes n ON n.id=x.note_id JOIN trips t ON t.id=n.trip_id
                    WHERE t.status='closed' AND x.created_at<?""", (evidence_iso,)).fetchone()[0],
                "audit": db.execute(
                    "SELECT COUNT(*) FROM audit WHERE happened_at<?", (audit_iso,)
                ).fetchone()[0],
            }
            if not apply:
                return {"applied": False, "candidates": counts, "deleted": {key: 0 for key in counts}}
            db.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (moment_iso,))
            db.execute("DELETE FROM driver_sessions WHERE expires_at<=?", (moment_iso,))
            db.execute("DELETE FROM login_limits WHERE window_started<?", (login_cutoff,))
            db.execute("""DELETE FROM evidence WHERE id IN (
                SELECT x.id FROM evidence x JOIN notes n ON n.id=x.note_id
                JOIN trips t ON t.id=n.trip_id WHERE t.status='closed' AND x.created_at<?
            )""", (evidence_iso,))
            db.execute("DELETE FROM audit WHERE happened_at<?", (audit_iso,))
            self._audit(db, "system:retention", "retention_applied", "system", 0, counts)
            return {"applied": True, "candidates": counts, "deleted": counts.copy()}

    def login_blocked(self, client: str, plate: str) -> bool:
        keys = login_limit_keys(client, plate)
        moment = time.time()
        with self.connection() as db:
            db.execute("DELETE FROM login_limits WHERE window_started<? AND COALESCE(blocked_until,0)<?",
                       (moment - 86400, moment))
            row = db.execute("SELECT 1 FROM login_limits WHERE key_hash IN (?,?) AND blocked_until>? LIMIT 1",
                             (*keys, moment)).fetchone()
            return row is not None

    def record_login_failure(self, client: str, plate: str) -> None:
        moment = time.time()
        block_seconds = 300 if str(plate).startswith("admin:") else 900
        with self.connection() as db:
            for key in login_limit_keys(client, plate):
                row = db.execute("SELECT failures,window_started FROM login_limits WHERE key_hash=?", (key,)).fetchone()
                failures = row["failures"] + 1 if row and row["window_started"] >= moment - 300 else 1
                window_started = row["window_started"] if row and row["window_started"] >= moment - 300 else moment
                blocked_until = moment + block_seconds if failures >= 5 else None
                db.execute("""INSERT INTO login_limits(key_hash,failures,window_started,blocked_until)
                    VALUES(?,?,?,?) ON CONFLICT(key_hash) DO UPDATE SET failures=excluded.failures,
                    window_started=excluded.window_started,blocked_until=excluded.blocked_until""",
                    (key, failures, window_started, blocked_until))

    def clear_login_failures(self, client: str, plate: str) -> None:
        keys = login_limit_keys(client, plate)
        with self.connection() as db:
            db.execute("DELETE FROM login_limits WHERE key_hash IN (?,?)", keys)

    @staticmethod
    def _audit(db, actor: str, event: str, kind: str, object_id: int, payload: dict):
        db.execute("INSERT INTO audit(happened_at, actor, event, object_type, object_id, payload) VALUES(?,?,?,?,?,?)",
                   (now(), actor, event, kind, object_id, json.dumps(payload, ensure_ascii=False)))

    def create_driver(self, name: str, plate: str, vehicle_type: str, *, actor: str = "admin") -> dict:
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
                self._audit(db, actor, "created", "driver", driver.lastrowid, {"plate": plate})
                return {"driver_id": driver.lastrowid, "vehicle_id": vehicle.lastrowid, "name": name, "plate": plate}
        except sqlite3.IntegrityError as exc:
            raise BusinessError("Placa já cadastrada") from exc

    def drivers(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""SELECT d.id AS driver_id,d.name,v.id AS vehicle_id,v.plate,v.vehicle_type,
                                d.active FROM drivers d JOIN vehicles v ON v.driver_id=d.id ORDER BY d.name""")
            return [dict(row) for row in rows]

    def create_trip(self, vehicle_id: int, label: str, *, actor: str = "admin") -> dict:
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
            self._audit(db, actor, "created", "trip", trip.lastrowid, {"label": label})
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

    def driver_productivity(self) -> list[dict]:
        """Indicadores administrativos por motorista sem transformar ocorrência em entrega."""
        with self.connection() as db:
            rows = db.execute("""SELECT d.id AS driver_id,d.name,v.plate,
                COUNT(DISTINCT t.id) AS trips,
                COUNT(DISTINCT CASE WHEN t.status='released' THEN t.id END) AS in_route,
                COUNT(DISTINCT CASE WHEN t.status='closed' THEN t.id END) AS closed_trips,
                COUNT(n.id) AS notes,
                SUM(CASE WHEN n.status='pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN n.status='delivered' THEN 1 ELSE 0 END) AS delivered,
                SUM(CASE WHEN n.status IN ('partial','refused','occurrence') THEN 1 ELSE 0 END) AS issues
                FROM drivers d JOIN vehicles v ON v.driver_id=d.id
                LEFT JOIN trips t ON t.driver_id=d.id AND t.vehicle_id=v.id
                LEFT JOIN notes n ON n.trip_id=t.id
                WHERE d.active=1 AND v.active=1
                GROUP BY d.id,d.name,v.id,v.plate ORDER BY d.name,v.plate""").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["delivery_rate"] = round(100 * item["delivered"] / item["notes"], 1) if item["notes"] else 0.0
                result.append(item)
            return result

    def trip_detail(self, trip_id: int) -> dict | None:
        """Conferência administrativa de NF-e individuais, inclusive no mesmo destino."""
        with self.connection() as db:
            trip = db.execute("""SELECT t.id,t.label,t.status,v.plate,d.name AS driver
                FROM trips t JOIN vehicles v ON v.id=t.vehicle_id
                JOIN drivers d ON d.id=t.driver_id WHERE t.id=?""", (trip_id,)).fetchone()
            if not trip:
                return None
            notes = db.execute("""SELECT n.id,n.number,n.recipient,n.address,n.city,n.seller,n.items_json,n.status,
                n.gross_weight,n.volumes FROM notes n WHERE n.trip_id=?
                ORDER BY n.id""", (trip_id,)).fetchall()
            events = db.execute("""SELECT e.id,e.note_id,e.status,e.reason,e.arrival_time,
                e.affected_item,e.seller_name,e.created_at,e.completed_at,e.occurrence_type,
                e.observation,e.expected_quantity,e.received_quantity,
                s.started_at,s.latitude,s.longitude,s.accuracy_m,
                d.name AS driver,CASE WHEN EXISTS (
                    SELECT 1 FROM evidence x WHERE x.event_id=e.id
                ) THEN 1 ELSE 0 END AS has_evidence
                FROM delivery_events e JOIN drivers d ON d.id=e.driver_id
                LEFT JOIN delivery_starts s ON s.id=e.start_id
                JOIN notes n ON n.id=e.note_id WHERE n.trip_id=?
                ORDER BY e.created_at,e.id""", (trip_id,)).fetchall()
            reversals = db.execute("""SELECT a.object_id AS note_id,a.happened_at,a.actor,a.payload
                FROM audit a JOIN notes n ON n.id=a.object_id
                WHERE a.object_type='note' AND a.event='delivery_reverted' AND n.trip_id=?
                ORDER BY a.happened_at,a.id""", (trip_id,)).fetchall()
            active_starts = db.execute("""SELECT s.note_id,s.started_at,s.latitude,s.longitude,s.accuracy_m
                FROM delivery_starts s LEFT JOIN delivery_events e ON e.start_id=s.id
                JOIN notes n ON n.id=s.note_id
                WHERE n.trip_id=? AND n.status='pending' AND e.id IS NULL
                ORDER BY s.recorded_at DESC""", (trip_id,)).fetchall()
            events_by_note: dict[int, list[dict]] = {}
            for event in events:
                item = dict(event)
                item["has_evidence"] = bool(item["has_evidence"])
                item["duration_seconds"] = None
                if item.get("started_at") and item.get("completed_at"):
                    try:
                        started = datetime.fromisoformat(item["started_at"].replace("Z", "+00:00"))
                        completed = datetime.fromisoformat(item["completed_at"].replace("Z", "+00:00"))
                        item["duration_seconds"] = max(0, int((completed - started).total_seconds()))
                    except ValueError:
                        pass
                events_by_note.setdefault(item.pop("note_id"), []).append(item)
            reversals_by_note: dict[int, list[dict]] = {}
            for reversal in reversals:
                item = dict(reversal)
                try:
                    item["payload"] = json.loads(item["payload"])
                except (TypeError, json.JSONDecodeError):
                    item["payload"] = {}
                reversals_by_note.setdefault(item.pop("note_id"), []).append(item)
            active_starts_by_note: dict[int, dict] = {}
            for start in active_starts:
                item = dict(start)
                active_starts_by_note.setdefault(item.pop("note_id"), item)
            result_notes = []
            for note in sorted(notes, key=lambda row: (invoice_sort_key(row["number"]), row["id"])):
                item = dict(note)
                item["items"] = json.loads(item.pop("items_json"))
                item["events"] = events_by_note.get(item["id"], [])
                item["reversals"] = reversals_by_note.get(item["id"], [])
                item["active_start"] = active_starts_by_note.get(item["id"])
                result_notes.append(item)
            return {**dict(trip), "notes": result_notes}

    def import_notes(self, trip_id: int, notes: list[dict], *, actor: str = "admin") -> dict:
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
                              city,seller,items_json,gross_weight,volumes) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                           (trip_id, note["chave"], note["numero"], note["destinatario_id"], note["destinatario"],
                            note["endereco"], note.get("cidade", ""), note.get("vendedor", ""),
                            json.dumps(note.get("itens", []), ensure_ascii=False), note["peso_bruto_kg"], note["volumes"]))
            self._audit(db, actor, "imported", "trip", trip_id, {"count": len(notes)})
            return {"trip_id": trip_id, "imported": len(notes)}

    def release_trip(self, trip_id: int, *, actor: str = "admin") -> dict:
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
            self._audit(db, actor, "released", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "released"}

    def set_driver_password(self, driver_id: int, password: str, *, actor: str = "admin") -> None:
        password = normalize_driver_pin(password)
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
            self._audit(db, actor, "password_reset", "driver", driver_id, {})

    def create_admin(self, username: str, password: str, role: str = "admin", *,
                     actor: str = "bootstrap") -> dict:
        username = normalize_admin_username(username)
        role = normalize_admin_role(role)
        if not 14 <= len(password) <= 256:
            raise BusinessError("A senha administrativa precisa ter entre 14 e 256 caracteres")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        with self.connection() as db:
            existing = db.execute("SELECT id FROM admin_users WHERE username=?", (username,)).fetchone()
            if existing:
                admin_id = existing["id"]
                db.execute("UPDATE admin_users SET salt=?,password_hash=?,role=?,active=1 WHERE id=?",
                           (salt.hex(), digest.hex(), role, admin_id))
                db.execute("DELETE FROM admin_sessions WHERE admin_id=?", (admin_id,))
                event = "admin_password_reset"
            else:
                admin_id = db.execute("""INSERT INTO admin_users(username,salt,password_hash,role,created_at)
                    VALUES(?,?,?,?,?)""", (username, salt.hex(), digest.hex(), role, now())).lastrowid
                event = "admin_created"
            self._audit(db, actor, event, "admin_user", admin_id, {"username": username, "role": role})
            return {"admin_id": admin_id, "username": username, "role": role, "active": True}

    def admin_users(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""SELECT id AS admin_id,username,role,active,created_at
                FROM admin_users ORDER BY username""")
            return [dict(row) for row in rows]

    def set_admin_access(self, admin_id: int, role: str, active: bool, *, actor: str,
                         current_admin_id: int | None = None) -> dict:
        role = normalize_admin_role(role)
        if not isinstance(active, bool):
            raise BusinessError("Estado da conta administrativa inválido")
        with self.connection() as db:
            target = db.execute("SELECT id,username,role,active FROM admin_users WHERE id=?",
                                (admin_id,)).fetchone()
            if not target:
                raise BusinessError("Administrador não encontrado")
            if current_admin_id == admin_id and (not active or role != "admin"):
                raise BusinessError("Não é permitido remover o próprio acesso administrativo")
            removes_admin = target["active"] and target["role"] == "admin" and (not active or role != "admin")
            if removes_admin:
                remaining = db.execute("""SELECT COUNT(*) FROM admin_users
                    WHERE active=1 AND role='admin' AND id<>?""", (admin_id,)).fetchone()[0]
                if not remaining:
                    raise BusinessError("Mantenha pelo menos um administrador ativo")
            db.execute("UPDATE admin_users SET role=?,active=? WHERE id=?", (role, int(active), admin_id))
            if not active:
                db.execute("DELETE FROM admin_sessions WHERE admin_id=?", (admin_id,))
            self._audit(db, actor, "admin_access_updated", "admin_user", admin_id,
                        {"role": role, "active": active})
            return {"admin_id": admin_id, "username": target["username"], "role": role, "active": active}

    def authenticate_admin(self, username: str, password: str) -> str:
        username = normalize_admin_username(username)
        with self.connection() as db:
            row = db.execute("""SELECT id,salt,password_hash FROM admin_users
                WHERE username=? AND active=1""", (username,)).fetchone()
            if not row:
                raise BusinessError("Credenciais inválidas")
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), 310_000)
            if not hmac.compare_digest(digest.hex(), row["password_hash"]):
                raise BusinessError("Credenciais inválidas")
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
            db.execute("DELETE FROM admin_sessions WHERE expires_at<=?", (now(),))
            db.execute("INSERT INTO admin_sessions(token_hash,admin_id,expires_at) VALUES(?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), row["id"], expires))
            return token

    def admin_by_token(self, token: str) -> int | None:
        identity = self.admin_identity_by_token(token)
        return identity["admin_id"] if identity else None

    def admin_identity_by_token(self, token: str) -> dict | None:
        if not token:
            return None
        with self.connection() as db:
            row = db.execute("""SELECT u.id AS admin_id,u.username,u.role FROM admin_sessions s
                JOIN admin_users u ON u.id=s.admin_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
                (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
            return dict(row) if row else None

    def revoke_admin_session(self, token: str) -> None:
        if not token:
            return
        with self.connection() as db:
            db.execute("DELETE FROM admin_sessions WHERE token_hash=?",
                       (hashlib.sha256(token.encode()).hexdigest(),))

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
            expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
            db.execute("DELETE FROM driver_sessions WHERE expires_at<=?", (now(),))
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

    def revoke_driver_session(self, token: str) -> None:
        if not token:
            return
        with self.connection() as db:
            db.execute("DELETE FROM driver_sessions WHERE token_hash=?",
                       (hashlib.sha256(token.encode()).hexdigest(),))

    def driver_trip(self, driver_id: int) -> dict | None:
        with self.connection() as db:
            trip = db.execute("""SELECT t.id,t.label,v.plate FROM trips t
                JOIN vehicles v ON v.id=t.vehicle_id
                WHERE t.driver_id=? AND t.status='released' ORDER BY t.id LIMIT 1""", (driver_id,)).fetchone()
            if not trip:
                return None
            notes = db.execute("""SELECT id,number,recipient,recipient_id,address,city,seller,items_json,status FROM notes
                WHERE trip_id=? AND status='pending' ORDER BY id""", (trip["id"],)).fetchall()
            total_notes = db.execute("SELECT COUNT(*) FROM notes WHERE trip_id=?", (trip["id"],)).fetchone()[0]
            starts = db.execute("""SELECT s.id,s.note_id,s.started_at,s.latitude,s.longitude,s.accuracy_m
                FROM delivery_starts s LEFT JOIN delivery_events e ON e.start_id=s.id
                JOIN notes n ON n.id=s.note_id
                WHERE n.trip_id=? AND e.id IS NULL ORDER BY s.recorded_at DESC""", (trip["id"],)).fetchall()
            starts_by_note = {}
            for start in starts:
                starts_by_note.setdefault(start["note_id"], dict(start))
            result_notes = []
            for note in sorted(notes, key=lambda row: (invoice_sort_key(row["number"]), row["id"])):
                item = dict(note)
                item["items"] = json.loads(item.pop("items_json"))
                item["start"] = starts_by_note.get(item["id"])
                result_notes.append(item)
            return {**dict(trip), "total_notes": total_notes, "notes": result_notes}

    def start_unloading(self, driver_id: int, note_id: int, start_id: str, started_at: str,
                        latitude=None, longitude=None, accuracy_m=None) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", str(start_id)):
            raise BusinessError("Identificador do início inválido")
        started = client_timestamp(started_at, "Horário de início")
        lat, lng, accuracy = normalized_location(latitude, longitude, accuracy_m)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM delivery_starts WHERE id=?", (start_id,)).fetchone()
            if existing:
                if existing["driver_id"] != driver_id or existing["note_id"] != note_id:
                    raise BusinessError("Identificador do início já utilizado")
                return {"start_id": start_id, "started_at": existing["started_at"],
                        "location_recorded": existing["latitude"] is not None, "duplicate": True}
            active = db.execute("""SELECT s.* FROM delivery_starts s
                LEFT JOIN delivery_events e ON e.start_id=s.id
                WHERE s.note_id=? AND s.driver_id=? AND e.id IS NULL
                ORDER BY s.recorded_at DESC LIMIT 1""", (note_id, driver_id)).fetchone()
            if active:
                return {"start_id": active["id"], "started_at": active["started_at"],
                        "location_recorded": active["latitude"] is not None, "duplicate": True}
            note = db.execute("""SELECT n.status,t.status AS trip_status FROM notes n
                JOIN trips t ON t.id=n.trip_id WHERE n.id=? AND t.driver_id=?""",
                (note_id, driver_id)).fetchone()
            if not note or note["trip_status"] != "released" or note["status"] != "pending":
                raise BusinessError("A entrega não está pendente nesta viagem")
            db.execute("""INSERT INTO delivery_starts(id,note_id,driver_id,started_at,recorded_at,
                latitude,longitude,accuracy_m) VALUES(?,?,?,?,?,?,?,?)""",
                (start_id, note_id, driver_id, started.isoformat(), now(), lat, lng, accuracy))
            self._audit(db, f"driver:{driver_id}", "unloading_started", "note", note_id,
                        {"start_id": start_id, "location_recorded": lat is not None})
            return {"start_id": start_id, "started_at": started.isoformat(),
                    "location_recorded": lat is not None, "duplicate": False}

    def reopen_note(self, note_id: int, reason: str, *, actor: str) -> dict:
        reason = str(reason or "").strip()
        if not 5 <= len(reason) <= 500:
            raise BusinessError("Informe o motivo do estorno com 5 a 500 caracteres")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            note = db.execute("""SELECT n.status,t.id AS trip_id,t.status AS trip_status,t.vehicle_id
                FROM notes n JOIN trips t ON t.id=n.trip_id WHERE n.id=?""", (note_id,)).fetchone()
            if not note:
                raise BusinessError("Nota não encontrada")
            if note["status"] == "pending":
                raise BusinessError("A nota já está pendente")
            if note["trip_status"] == "closed":
                other = db.execute("""SELECT 1 FROM trips WHERE vehicle_id=? AND status='released'
                    AND id<>?""", (note["vehicle_id"], note["trip_id"])).fetchone()
                if other:
                    raise BusinessError("Existe outra viagem ativa para este veículo")
                db.execute("UPDATE trips SET status='released',closed_at=NULL WHERE id=?", (note["trip_id"],))
                self._audit(db, actor, "reopened", "trip", note["trip_id"], {"reason": reason})
            elif note["trip_status"] != "released":
                raise BusinessError("Somente viagens liberadas ou encerradas podem receber estorno")
            db.execute("UPDATE notes SET status='pending' WHERE id=?", (note_id,))
            self._audit(db, actor, "delivery_reverted", "note", note_id,
                        {"from": note["status"], "to": "pending", "reason": reason})
            return {"note_id": note_id, "trip_id": note["trip_id"], "status": "pending"}

    def evidence_access(self, event_id: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise BusinessError("Identificador da evidência inválido")
        with self.connection() as db:
            row = db.execute("SELECT mime,content FROM evidence WHERE event_id=?", (event_id,)).fetchone()
            if not row:
                return None
            encoded = base64.b64encode(row["content"]).decode()
            return {"data_url": f"data:{row['mime']};base64,{encoded}"}

    def record_delivery(self, driver_id: int, note_id: int, event_id: str, status: str, reason: str,
                        photo_base64: str | None = None, arrival_time: str = "", affected_item: str = "",
                        seller_name: str = "", *, start_id: str = "", completed_at: str = "",
                        occurrence_type: str = "", observation: str = "", expected_quantity: str = "",
                        received_quantity: str = "") -> dict:
        if status not in DELIVERY_STATUSES:
            raise BusinessError("Situação inválida")
        if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise BusinessError("Identificador da operação inválido")
        reason = str(reason or "").strip()
        observation = str(observation or "").strip()
        occurrence_type = str(occurrence_type or "").strip()
        if len(reason) > 1000 or len(observation) > 1000:
            raise BusinessError("Motivo ou observação muito longos")
        if arrival_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", arrival_time):
            raise BusinessError("Horário de chegada inválido")
        if len(affected_item) > 500 or len(seller_name) > 100:
            raise BusinessError("Detalhes da ocorrência muito longos")
        photo = None
        photo_type = None
        if photo_base64:
            try:
                photo = base64.b64decode(photo_base64, validate=True)
                photo_type, _extension = detect_image(photo)
            except (ValueError, binascii.Error, StorageError) as exc:
                raise BusinessError("Fotografia inválida; envie JPEG ou PNG até 5 MB") from exc
        with self.connection() as db:
            # Serializa a confirmação de um mesmo event_id no SQLite. Assim, dois
            # reenvios concorrentes resultam em uma gravação e uma resposta duplicada.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("""SELECT e.id,e.note_id,e.driver_id,e.status,e.reason,e.arrival_time,
                e.affected_item,e.seller_name,e.observation,e.created_at,n.number,n.recipient,n.city,n.seller,
                v.plate,d.name AS driver FROM delivery_events e
                JOIN notes n ON n.id=e.note_id JOIN trips t ON t.id=n.trip_id
                JOIN vehicles v ON v.id=t.vehicle_id JOIN drivers d ON d.id=e.driver_id
                WHERE e.id=?""", (event_id,)).fetchone()
            if existing:
                if existing["driver_id"] != driver_id or existing["note_id"] != note_id:
                    raise BusinessError("Identificador de operação já utilizado")
                existing_item = dict(existing)
                message = occurrence_message(
                    existing_item, existing["status"], existing["reason"], existing["created_at"],
                    existing["arrival_time"], existing["affected_item"], existing["seller_name"],
                    existing_item.get("observation", ""),
                )
                return {"event_id": event_id, "duplicate": True,
                        "status": existing["status"], "share_text": message}
            if status == "delivered" and photo is None:
                raise BusinessError("Fotografia do canhoto é obrigatória para marcar entrega concluída")
            note = db.execute("""SELECT n.id,n.number,n.recipient,n.city,n.seller,n.items_json,n.status,
                t.status AS trip_status,v.plate,d.name AS driver FROM notes n
                JOIN trips t ON t.id=n.trip_id JOIN vehicles v ON v.id=t.vehicle_id
                JOIN drivers d ON d.id=t.driver_id WHERE n.id=? AND t.driver_id=?""",
                (note_id, driver_id)).fetchone()
            if not note or note["trip_status"] != "released":
                raise BusinessError("Nota não pertence à viagem ativa deste motorista")
            if note["status"] != "pending":
                raise BusinessError("Baixa já registrada; somente um administrador pode estornar")

            start = None
            if start_id:
                if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", str(start_id)):
                    raise BusinessError("Identificador do início inválido")
                start = db.execute("""SELECT id,started_at FROM delivery_starts
                    WHERE id=? AND note_id=? AND driver_id=?""", (start_id, note_id, driver_id)).fetchone()
                if not start:
                    raise BusinessError("Início do descarregamento não encontrado")
                linked = db.execute("SELECT id FROM delivery_events WHERE start_id=?", (start_id,)).fetchone()
                if linked:
                    raise BusinessError("Este descarregamento já possui uma baixa")
                start_moment = datetime.fromisoformat(start["started_at"].replace("Z", "+00:00"))
                arrival_time = start_moment.astimezone(timezone(timedelta(hours=-3))).strftime("%H:%M")
            elif not arrival_time:
                raise BusinessError("Inicie o descarregamento antes de concluir a entrega")

            completed = client_timestamp(completed_at, "Horário de conclusão") \
                if completed_at else datetime.now(timezone.utc)
            if start:
                start_moment = datetime.fromisoformat(start["started_at"].replace("Z", "+00:00"))
                duration_seconds = int((completed - start_moment).total_seconds())
                if duration_seconds < -60 or duration_seconds > 7 * 24 * 3600:
                    raise BusinessError("Intervalo de descarregamento inválido")
                duration_seconds = max(0, duration_seconds)
            else:
                duration_seconds = None

            affected_description = ""
            if status == "refused":
                if reason not in REFUSAL_REASONS:
                    raise BusinessError("Selecione Pedido Duplicado ou Sem Pedido para a recusa")
                occurrence_type = ""
            elif status == "occurrence":
                if occurrence_type not in OCCURRENCE_TYPES:
                    raise BusinessError("Selecione o tipo da ocorrência")
                if occurrence_type == "general":
                    if len(reason) < 3:
                        raise BusinessError("Descreva o motivo da ocorrência")
                else:
                    items = json.loads(note["items_json"] or "[]")
                    item_index = re.fullmatch(r"__index_(\d+)", str(affected_item))
                    if item_index:
                        try:
                            selected = items[int(item_index.group(1))]
                        except IndexError:
                            selected = None
                    else:
                        selected = next((item for item in items if str(item.get("codigo", "")) == str(affected_item)), None)
                    if not selected:
                        raise BusinessError("Selecione um item válido da NF-e")
                    xml_quantity = normalized_quantity(selected.get("quantidade", ""), "Quantidade da NF-e")
                    informed_expected = normalized_quantity(expected_quantity, "Quantidade da NF-e")
                    received = normalized_quantity(received_quantity, "Quantidade física")
                    if informed_expected != xml_quantity:
                        raise BusinessError("A quantidade total não corresponde ao XML")
                    if received >= xml_quantity:
                        raise BusinessError("Na falta parcial, a quantidade física deve ser menor que a quantidade da NF-e")
                    unit = str(selected.get("unidade", "")).strip()
                    description = str(selected.get("descricao", "Produto")).strip()
                    affected_description = (f"{description} • Previsto: {expected_quantity} {unit} • "
                                            f"Recebido: {received_quantity} {unit}").strip()
                    reason = "Falta parcial"
            else:
                occurrence_type = ""
                reason = ""

            created_at = now()
            db.execute("""INSERT INTO delivery_events(id,note_id,driver_id,status,reason,arrival_time,
                affected_item,seller_name,start_id,completed_at,occurrence_type,observation,
                expected_quantity,received_quantity,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, note_id, driver_id, status, reason, arrival_time,
                 affected_description, seller_name.strip(), start_id or None, completed.isoformat(),
                 occurrence_type, observation, str(expected_quantity or ""),
                 str(received_quantity or ""), created_at))
            if photo is not None:
                db.execute("""INSERT INTO evidence(note_id,event_id,mime,sha256,content,created_at)
                    VALUES(?,?,?,?,?,?)""", (note_id, event_id, photo_type, hashlib.sha256(photo).hexdigest(), photo, now()))
            db.execute("UPDATE notes SET status=? WHERE id=?", (status, note_id))
            self._audit(db, f"driver:{driver_id}", "status_changed", "note", note_id,
                        {"from": note["status"], "to": status, "reason": reason, "event_id": event_id,
                         "start_id": start_id or None, "occurrence_type": occurrence_type})
            message = occurrence_message(dict(note), status, reason, created_at, arrival_time,
                                         affected_description, seller_name, observation)
            return {"event_id": event_id, "duplicate": False, "status": status,
                    "duration_seconds": duration_seconds, "share_text": message}

    def close_driver_trip(self, driver_id: int, trip_id: int) -> dict:
        with self.connection() as db:
            trip = db.execute("SELECT id,status FROM trips WHERE id=? AND driver_id=?", (trip_id, driver_id)).fetchone()
            if trip and trip["status"] == "closed":
                return {"trip_id": trip_id, "status": "closed", "duplicate": True}
            if not trip or trip["status"] != "released":
                raise BusinessError("Viagem não está ativa para este motorista")
            pending = db.execute("SELECT COUNT(*) FROM notes WHERE trip_id=? AND status='pending'", (trip_id,)).fetchone()[0]
            if pending:
                raise BusinessError(f"Existem {pending} NF-e sem baixa ou ocorrência")
            db.execute("UPDATE trips SET status='closed',closed_at=? WHERE id=?", (now(), trip_id))
            self._audit(db, f"driver:{driver_id}", "closed", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "closed", "duplicate": False}
