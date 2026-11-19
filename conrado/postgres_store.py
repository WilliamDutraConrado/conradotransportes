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

from conrado.store import (
    BusinessError, login_limit_keys, normalize_admin_role, normalize_admin_username, normalize_plate,
    occurrence_message,
)
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

    def healthcheck(self) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 AS healthy").fetchone()["healthy"] == 1

    def diagnostics(self) -> dict:
        """Indicadores técnicos agregados, sem retornar dados operacionais."""
        with self.connection() as db:
            row = db.execute("""SELECT
                (SELECT COUNT(*) FROM app.trips WHERE status='released') AS active_trips,
                (SELECT COUNT(*) FROM app.notes WHERE status='pending') AS pending_notes,
                (SELECT COUNT(*) FROM app.evidence) AS evidence,
                (SELECT COUNT(*) FROM app.admin_sessions WHERE expires_at<=now()) AS expired_admin_sessions,
                (SELECT COUNT(*) FROM app.driver_sessions WHERE expires_at<=now()) AS expired_driver_sessions,
                (SELECT COUNT(*) FROM app.evidence x WHERE NOT EXISTS (
                    SELECT 1 FROM app.delivery_events e WHERE e.id=x.event_id)) AS orphan_evidence,
                (SELECT MAX(created_at) FROM app.delivery_events) AS last_event_at,
                (SELECT MAX(happened_at) FROM app.audit) AS last_audit_at""").fetchone()
            result = dict(row)
            result["database"] = "ok"
            for name in ("last_event_at", "last_audit_at"):
                if result[name] is not None:
                    result[name] = result[name].isoformat()
            return result

    def audit_export(self, limit: int = 50_000) -> list[dict]:
        if not 1 <= limit <= 50_000:
            raise BusinessError("Limite de exportação inválido")
        with self.connection() as db:
            rows = db.execute("""SELECT happened_at,actor,event,object_type,object_id,payload
                FROM app.audit ORDER BY happened_at,id LIMIT %s""", (limit,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["happened_at"] = item["happened_at"].isoformat()
                item["payload"] = json.dumps(item["payload"], ensure_ascii=False, sort_keys=True)
                result.append(item)
            return result

    def occurrence_export(self, limit: int = 50_000) -> list[dict]:
        if not 1 <= limit <= 50_000:
            raise BusinessError("Limite de exportação inválido")
        with self.connection() as db:
            rows = db.execute("""SELECT e.created_at,e.status,e.reason,e.arrival_time,e.affected_item,
                e.seller_name,n.number AS invoice,n.recipient,n.city,t.label AS trip,v.plate,
                d.name AS driver,(x.id IS NOT NULL) AS has_evidence
                FROM app.delivery_events e JOIN app.notes n ON n.id=e.note_id
                JOIN app.trips t ON t.id=n.trip_id JOIN app.vehicles v ON v.id=t.vehicle_id
                JOIN app.drivers d ON d.id=e.driver_id LEFT JOIN app.evidence x ON x.event_id=e.id
                WHERE e.status<>'delivered' ORDER BY e.created_at,e.id LIMIT %s""", (limit,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["created_at"] = item["created_at"].isoformat()
                result.append(item)
            return result

    def cleanup_retention(self, *, now_at: datetime, evidence_before: datetime,
                          audit_before: datetime, apply: bool = False) -> dict:
        """Aplica retenção em viagens encerradas e remove sessões já vencidas."""
        moment = now_at.astimezone(timezone.utc)
        login_cutoff = moment - timedelta(days=1)
        with self.connection() as db:
            evidence_rows = db.execute("""SELECT x.id,x.storage_path FROM app.evidence x
                JOIN app.notes n ON n.id=x.note_id JOIN app.trips t ON t.id=n.trip_id
                WHERE t.status='closed' AND x.created_at<%s ORDER BY x.id""", (evidence_before,)).fetchall()
            counts = {
                "admin_sessions": db.execute(
                    "SELECT COUNT(*) AS total FROM app.admin_sessions WHERE expires_at<=%s", (moment,)
                ).fetchone()["total"],
                "driver_sessions": db.execute(
                    "SELECT COUNT(*) AS total FROM app.driver_sessions WHERE expires_at<=%s", (moment,)
                ).fetchone()["total"],
                "login_limits": db.execute(
                    "SELECT COUNT(*) AS total FROM app.login_limits WHERE window_started<%s", (login_cutoff,)
                ).fetchone()["total"],
                "evidence": len(evidence_rows),
                "audit": db.execute(
                    "SELECT COUNT(*) AS total FROM app.audit WHERE happened_at<%s", (audit_before,)
                ).fetchone()["total"],
            }
        if not apply:
            return {"applied": False, "candidates": counts, "deleted": {key: 0 for key in counts}}
        try:
            for row in evidence_rows:
                self.storage.delete(row["storage_path"])
        except StorageError as exc:
            raise BusinessError("Retenção interrompida: não foi possível remover um canhoto privado") from exc
        with self.connection() as db:
            db.execute("DELETE FROM app.admin_sessions WHERE expires_at<=%s", (moment,))
            db.execute("DELETE FROM app.driver_sessions WHERE expires_at<=%s", (moment,))
            db.execute("DELETE FROM app.login_limits WHERE window_started<%s", (login_cutoff,))
            if evidence_rows:
                db.execute("DELETE FROM app.evidence WHERE id=ANY(%s)",
                           ([row["id"] for row in evidence_rows],))
            db.execute("DELETE FROM app.audit WHERE happened_at<%s", (audit_before,))
            self._audit(db, "system:retention", "retention_applied", "system", 0, counts)
        return {"applied": True, "candidates": counts, "deleted": counts.copy()}

    def login_blocked(self, client: str, plate: str) -> bool:
        keys = list(login_limit_keys(client, plate))
        with self.connection() as db:
            db.execute("""DELETE FROM app.login_limits WHERE window_started<now()-interval '1 day'
                AND COALESCE(blocked_until,'epoch'::timestamptz)<now()""")
            return db.execute("""SELECT 1 FROM app.login_limits
                WHERE key_hash=ANY(%s) AND blocked_until>now() LIMIT 1""", (keys,)).fetchone() is not None

    def record_login_failure(self, client: str, plate: str) -> None:
        with self.connection() as db:
            for key in login_limit_keys(client, plate):
                db.execute("""INSERT INTO app.login_limits(key_hash,failures,window_started,blocked_until)
                    VALUES (%s,1,now(),NULL) ON CONFLICT(key_hash) DO UPDATE SET
                    failures=CASE WHEN app.login_limits.window_started<now()-interval '5 minutes'
                        THEN 1 ELSE LEAST(app.login_limits.failures+1,100) END,
                    window_started=CASE WHEN app.login_limits.window_started<now()-interval '5 minutes'
                        THEN now() ELSE app.login_limits.window_started END,
                    blocked_until=CASE
                        WHEN app.login_limits.window_started<now()-interval '5 minutes' THEN NULL
                        WHEN app.login_limits.failures+1>=5 THEN now()+interval '5 minutes'
                        ELSE NULL END""", (key,))

    def clear_login_failures(self, client: str, plate: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM app.login_limits WHERE key_hash=ANY(%s)",
                       (list(login_limit_keys(client, plate)),))

    @contextmanager
    def _storage_compensation(self):
        uploaded = {}
        try:
            yield uploaded
        except Exception as exc:
            path = uploaded.get("path")
            if path:
                try:
                    self.storage.delete(path)
                except Exception as cleanup_exc:
                    raise BusinessError(
                        "A operação falhou e a remoção automática da foto precisa ser verificada"
                    ) from cleanup_exc
            raise

    @staticmethod
    def _audit(db, actor, event, kind, object_id, payload):
        db.execute("INSERT INTO app.audit(actor,event,object_type,object_id,payload) VALUES (%s,%s,%s,%s,%s::jsonb)",
                   (actor, event, kind, object_id, json.dumps(payload, ensure_ascii=False)))

    def create_driver(self, name, plate, vehicle_type, *, actor="admin"):
        name, plate, vehicle_type = name.strip(), normalize_plate(plate), vehicle_type.strip()
        if not 2 <= len(name) <= 100 or not 1 <= len(vehicle_type) <= 30:
            raise BusinessError("Informe nome e tipo de veículo válidos")
        with self.connection() as db:
            if db.execute("SELECT 1 FROM app.vehicles WHERE plate=%s", (plate,)).fetchone():
                raise BusinessError("Placa já cadastrada")
            driver_id = db.execute("INSERT INTO app.drivers(name) VALUES (%s) RETURNING id", (name,)).fetchone()["id"]
            vehicle_id = db.execute("INSERT INTO app.vehicles(plate,vehicle_type,driver_id) VALUES (%s,%s,%s) RETURNING id",
                                    (plate, vehicle_type, driver_id)).fetchone()["id"]
            self._audit(db, actor, "created", "driver", driver_id, {"plate": plate})
            return {"driver_id": driver_id, "vehicle_id": vehicle_id, "name": name, "plate": plate}

    def drivers(self):
        with self.connection() as db:
            rows = db.execute("""SELECT d.id AS driver_id,d.name,v.id AS vehicle_id,v.plate,v.vehicle_type,d.active
                FROM app.drivers d JOIN app.vehicles v ON v.driver_id=d.id ORDER BY d.name""").fetchall()
            return [dict(row) for row in rows]

    def create_trip(self, vehicle_id, label, *, actor="admin"):
        label = label.strip()
        if not 2 <= len(label) <= 120:
            raise BusinessError("Informe uma identificação para a viagem")
        with self.connection() as db:
            vehicle = db.execute("SELECT driver_id,active FROM app.vehicles WHERE id=%s", (vehicle_id,)).fetchone()
            if not vehicle or not vehicle["active"]:
                raise BusinessError("Veículo inexistente ou inativo")
            trip_id = db.execute("INSERT INTO app.trips(vehicle_id,driver_id,label) VALUES (%s,%s,%s) RETURNING id",
                                 (vehicle_id, vehicle["driver_id"], label)).fetchone()["id"]
            self._audit(db, actor, "created", "trip", trip_id, {"label": label})
            return {"trip_id": trip_id, "status": "draft"}

    def trips(self):
        with self.connection() as db:
            rows = db.execute("""SELECT t.id,t.label,t.status,v.plate,d.name AS driver,COUNT(n.id) AS notes
                FROM app.trips t JOIN app.vehicles v ON v.id=t.vehicle_id
                JOIN app.drivers d ON d.id=t.driver_id LEFT JOIN app.notes n ON n.trip_id=t.id
                GROUP BY t.id,v.plate,d.name ORDER BY t.id DESC""").fetchall()
            return [dict(row) for row in rows]

    def summary(self):
        with self.connection() as db:
            trips = {row["status"]: row["total"] for row in db.execute(
                "SELECT status,COUNT(*) AS total FROM app.trips GROUP BY status").fetchall()}
            notes = {row["status"]: row["total"] for row in db.execute(
                "SELECT status,COUNT(*) AS total FROM app.notes GROUP BY status").fetchall()}
            return {
                "trips": {key: trips.get(key, 0) for key in ("draft", "released", "closed")},
                "notes": {key: notes.get(key, 0) for key in ("pending", "delivered", "partial", "refused", "occurrence")},
            }

    def driver_productivity(self):
        with self.connection() as db:
            rows = db.execute("""SELECT d.id AS driver_id,d.name,v.plate,
                COUNT(DISTINCT t.id) AS trips,
                COUNT(DISTINCT CASE WHEN t.status='released' THEN t.id END) AS in_route,
                COUNT(DISTINCT CASE WHEN t.status='closed' THEN t.id END) AS closed_trips,
                COUNT(n.id) AS notes,
                SUM(CASE WHEN n.status='pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN n.status='delivered' THEN 1 ELSE 0 END) AS delivered,
                SUM(CASE WHEN n.status IN ('partial','refused','occurrence') THEN 1 ELSE 0 END) AS issues
                FROM app.drivers d JOIN app.vehicles v ON v.driver_id=d.id
                LEFT JOIN app.trips t ON t.driver_id=d.id AND t.vehicle_id=v.id
                LEFT JOIN app.notes n ON n.trip_id=t.id
                WHERE d.active=true AND v.active=true
                GROUP BY d.id,d.name,v.id,v.plate ORDER BY d.name,v.plate""").fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["delivery_rate"] = round(100 * item["delivered"] / item["notes"], 1) if item["notes"] else 0.0
                result.append(item)
            return result

    def trip_detail(self, trip_id):
        with self.connection() as db:
            trip = db.execute("""SELECT t.id,t.label,t.status,v.plate,d.name AS driver
                FROM app.trips t JOIN app.vehicles v ON v.id=t.vehicle_id
                JOIN app.drivers d ON d.id=t.driver_id WHERE t.id=%s""", (trip_id,)).fetchone()
            if not trip:
                return None
            notes = db.execute("""SELECT n.id,n.number,n.recipient,n.address,n.city,n.seller,n.items,n.status,
                n.gross_weight,n.volumes FROM app.notes n WHERE n.trip_id=%s
                ORDER BY n.recipient,n.address,n.number,n.id""", (trip_id,)).fetchall()
            events = db.execute("""SELECT e.id,e.note_id,e.status,e.reason,e.arrival_time,
                e.affected_item,e.seller_name,e.created_at::text AS created_at,
                d.name AS driver,EXISTS (
                    SELECT 1 FROM app.evidence x WHERE x.event_id=e.id
                ) AS has_evidence
                FROM app.delivery_events e JOIN app.drivers d ON d.id=e.driver_id
                JOIN app.notes n ON n.id=e.note_id WHERE n.trip_id=%s
                ORDER BY e.created_at,e.id""", (trip_id,)).fetchall()
            events_by_note = {}
            for event in events:
                item = dict(event)
                events_by_note.setdefault(item.pop("note_id"), []).append(item)
            result_notes = []
            for note in notes:
                item = dict(note)
                item["events"] = events_by_note.get(item["id"], [])
                result_notes.append(item)
            return {**dict(trip), "notes": result_notes}

    def import_notes(self, trip_id, notes, *, actor="admin"):
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
                db.execute("""INSERT INTO app.notes(trip_id,access_key,number,recipient_id,recipient,address,
                    city,seller,items,gross_weight,volumes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                    (trip_id, n["chave"], n["numero"], n["destinatario_id"], n["destinatario"], n["endereco"],
                     n.get("cidade", ""), n.get("vendedor", ""), json.dumps(n.get("itens", []), ensure_ascii=False),
                     n["peso_bruto_kg"], n["volumes"]))
            self._audit(db, actor, "imported", "trip", trip_id, {"count": len(notes)})
            return {"trip_id": trip_id, "imported": len(notes)}

    def release_trip(self, trip_id, *, actor="admin"):
        with self.connection() as db:
            trip = db.execute("SELECT vehicle_id,status FROM app.trips WHERE id=%s FOR UPDATE", (trip_id,)).fetchone()
            if not trip or trip["status"] != "draft":
                raise BusinessError("Somente uma viagem em rascunho pode ser liberada")
            if not db.execute("SELECT 1 FROM app.notes WHERE trip_id=%s", (trip_id,)).fetchone():
                raise BusinessError("Não é possível liberar viagem sem notas")
            if db.execute("SELECT 1 FROM app.trips WHERE vehicle_id=%s AND status='released'", (trip["vehicle_id"],)).fetchone():
                raise BusinessError("Já existe viagem liberada para este veículo")
            db.execute("UPDATE app.trips SET status='released',released_at=now() WHERE id=%s", (trip_id,))
            self._audit(db, actor, "released", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "released"}

    def set_driver_password(self, driver_id, password, *, actor="admin"):
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
            row = db.execute("SELECT id FROM app.admin_users WHERE username=%s FOR UPDATE", (username,)).fetchone()
            if row:
                admin_id = row["id"]
                db.execute("UPDATE app.admin_users SET salt=%s,password_hash=%s,role=%s,active=true WHERE id=%s",
                           (salt.hex(), digest.hex(), role, admin_id))
                db.execute("DELETE FROM app.admin_sessions WHERE admin_id=%s", (admin_id,))
                event = "admin_password_reset"
            else:
                admin_id = db.execute("""INSERT INTO app.admin_users(username,salt,password_hash,role)
                    VALUES(%s,%s,%s,%s) RETURNING id""",
                    (username, salt.hex(), digest.hex(), role)).fetchone()["id"]
                event = "admin_created"
            self._audit(db, actor, event, "admin_user", admin_id, {"username": username, "role": role})
            return {"admin_id": admin_id, "username": username, "role": role, "active": True}

    def admin_users(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("""SELECT id AS admin_id,username,role,active,
                created_at::text AS created_at
                FROM app.admin_users ORDER BY username""").fetchall()
            return [dict(row) for row in rows]

    def set_admin_access(self, admin_id: int, role: str, active: bool, *, actor: str,
                         current_admin_id: int | None = None) -> dict:
        role = normalize_admin_role(role)
        if not isinstance(active, bool):
            raise BusinessError("Estado da conta administrativa inválido")
        with self.connection() as db:
            target = db.execute("""SELECT id,username,role,active FROM app.admin_users
                WHERE id=%s FOR UPDATE""", (admin_id,)).fetchone()
            if not target:
                raise BusinessError("Administrador não encontrado")
            if current_admin_id == admin_id and (not active or role != "admin"):
                raise BusinessError("Não é permitido remover o próprio acesso administrativo")
            removes_admin = target["active"] and target["role"] == "admin" and (not active or role != "admin")
            if removes_admin:
                remaining = db.execute("""SELECT COUNT(*) AS total FROM app.admin_users
                    WHERE active=true AND role='admin' AND id<>%s""", (admin_id,)).fetchone()["total"]
                if not remaining:
                    raise BusinessError("Mantenha pelo menos um administrador ativo")
            db.execute("UPDATE app.admin_users SET role=%s,active=%s WHERE id=%s", (role, active, admin_id))
            if not active:
                db.execute("DELETE FROM app.admin_sessions WHERE admin_id=%s", (admin_id,))
            self._audit(db, actor, "admin_access_updated", "admin_user", admin_id,
                        {"role": role, "active": active})
            return {"admin_id": admin_id, "username": target["username"], "role": role, "active": active}

    def authenticate_admin(self, username: str, password: str) -> str:
        username = normalize_admin_username(username)
        with self.connection() as db:
            row = db.execute("""SELECT id,salt,password_hash FROM app.admin_users
                WHERE username=%s AND active=true""", (username,)).fetchone()
            if not row:
                raise BusinessError("Credenciais inválidas")
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(row["salt"]), 310_000)
            if not hmac.compare_digest(digest.hex(), row["password_hash"]):
                raise BusinessError("Credenciais inválidas")
            token = secrets.token_urlsafe(32)
            db.execute("DELETE FROM app.admin_sessions WHERE expires_at<=now()")
            db.execute("INSERT INTO app.admin_sessions(token_hash,admin_id,expires_at) VALUES (%s,%s,%s)",
                       (hashlib.sha256(token.encode()).hexdigest(), row["id"],
                        datetime.now(timezone.utc) + timedelta(hours=8)))
            return token

    def admin_by_token(self, token: str) -> int | None:
        identity = self.admin_identity_by_token(token)
        return identity["admin_id"] if identity else None

    def admin_identity_by_token(self, token: str) -> dict | None:
        if not token:
            return None
        with self.connection() as db:
            row = db.execute("""SELECT u.id AS admin_id,u.username,u.role FROM app.admin_sessions s
                JOIN app.admin_users u ON u.id=s.admin_id
                WHERE s.token_hash=%s AND s.expires_at>now() AND u.active=true""",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return dict(row) if row else None

    def revoke_admin_session(self, token: str) -> None:
        if not token:
            return
        with self.connection() as db:
            db.execute("DELETE FROM app.admin_sessions WHERE token_hash=%s",
                       (hashlib.sha256(token.encode()).hexdigest(),))

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
            db.execute("DELETE FROM app.driver_sessions WHERE expires_at<=now()")
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

    def revoke_driver_session(self, token):
        if not token:
            return
        with self.connection() as db:
            db.execute("DELETE FROM app.driver_sessions WHERE token_hash=%s",
                       (hashlib.sha256(token.encode()).hexdigest(),))

    def driver_trip(self, driver_id):
        with self.connection() as db:
            trip = db.execute("""SELECT t.id,t.label,v.plate FROM app.trips t JOIN app.vehicles v
                ON v.id=t.vehicle_id WHERE t.driver_id=%s AND t.status='released' ORDER BY t.id LIMIT 1""",
                (driver_id,)).fetchone()
            if not trip:
                return None
            notes = db.execute("""SELECT id,number,recipient,recipient_id,address,city,seller,items,status FROM app.notes
                WHERE trip_id=%s ORDER BY recipient,address,number""", (trip["id"],)).fetchall()
            return {**dict(trip), "notes": [dict(n) for n in notes]}

    def evidence_access(self, event_id: str) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise BusinessError("Identificador da evidência inválido")
        with self.connection() as db:
            row = db.execute("SELECT storage_path FROM app.evidence WHERE event_id=%s", (event_id,)).fetchone()
        if not row:
            return None
        return {"url": self.storage.signed_download_url(row["storage_path"], 300), "expires_in": 300}

    def record_delivery(self, driver_id, note_id, event_id, status, reason, photo_base64=None,
                        arrival_time="", affected_item="", seller_name=""):
        if status not in ("delivered", "partial", "refused", "occurrence"):
            raise BusinessError("Situação inválida")
        if not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise BusinessError("Identificador da operação inválido")
        if len(reason) > 1000 or (status != "delivered" and len(reason.strip()) < 3):
            raise BusinessError("Informe um motivo válido para a ocorrência")
        if arrival_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", arrival_time):
            raise BusinessError("Horário de chegada inválido")
        if len(affected_item) > 500 or len(seller_name) > 100:
            raise BusinessError("Detalhes da ocorrência muito longos")
        photo = None
        if photo_base64:
            try:
                photo = base64.b64decode(photo_base64, validate=True)
                detect_image(photo)
            except (ValueError, binascii.Error, StorageError) as exc:
                raise BusinessError("Fotografia inválida; envie JPEG ou PNG até 5 MB") from exc
        with self._storage_compensation() as uploaded_state, self.connection() as db:
            # Serializa apenas reenvios do mesmo identificador. A segunda
            # transação aguarda a primeira e então encontra o evento existente.
            db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (event_id,))
            existing = db.execute("""SELECT e.note_id,e.driver_id,e.status,e.reason,e.arrival_time,
                e.affected_item,e.seller_name,e.created_at,n.number,n.recipient,n.city,n.seller,
                v.plate,d.name AS driver FROM app.delivery_events e
                JOIN app.notes n ON n.id=e.note_id JOIN app.trips t ON t.id=n.trip_id
                JOIN app.vehicles v ON v.id=t.vehicle_id JOIN app.drivers d ON d.id=e.driver_id
                WHERE e.id=%s""", (event_id,)).fetchone()
            if existing:
                if existing["note_id"] != note_id or existing["driver_id"] != driver_id:
                    raise BusinessError("Identificador de operação já utilizado")
                message = occurrence_message(
                    dict(existing), existing["status"], existing["reason"], existing["created_at"],
                    existing["arrival_time"], existing["affected_item"], existing["seller_name"],
                )
                return {"event_id": event_id, "duplicate": True,
                        "status": existing["status"], "share_text": message}
            if status == "delivered" and photo is None:
                raise BusinessError("Fotografia do canhoto é obrigatória para marcar entrega concluída")
            note = db.execute("""SELECT n.status,n.number,n.recipient,n.city,n.seller,t.id AS trip_id,
                t.status AS trip_status,v.plate,d.name AS driver FROM app.notes n
                JOIN app.trips t ON t.id=n.trip_id JOIN app.vehicles v ON v.id=t.vehicle_id
                JOIN app.drivers d ON d.id=t.driver_id
                WHERE n.id=%s AND t.driver_id=%s FOR UPDATE OF n""",
                (note_id, driver_id)).fetchone()
            if not note or note["trip_status"] != "released":
                raise BusinessError("Nota não pertence à viagem ativa deste motorista")
            created_at = datetime.now(timezone.utc)
            db.execute("""INSERT INTO app.delivery_events(id,note_id,driver_id,status,reason,arrival_time,
                affected_item,seller_name,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (event_id, note_id, driver_id, status, reason.strip(), arrival_time,
                 affected_item.strip(), seller_name.strip(), created_at))
            if photo is not None:
                try:
                    uploaded = self.storage.upload(note["trip_id"], note_id, event_id, photo)
                except StorageError as exc:
                    raise BusinessError("Não foi possível salvar a foto; tente novamente") from exc
                uploaded_state["path"] = uploaded.path
                db.execute("""INSERT INTO app.evidence(note_id,event_id,storage_path,mime,sha256)
                    VALUES (%s,%s,%s,%s,%s)""", (note_id, event_id, uploaded.path, uploaded.mime, uploaded.sha256))
            db.execute("UPDATE app.notes SET status=%s WHERE id=%s", (status, note_id))
            self._audit(db, f"driver:{driver_id}", "status_changed", "note", note_id,
                        {"from": note["status"], "to": status, "reason": reason.strip(), "event_id": event_id,
                         "arrival_time": arrival_time, "affected_item": affected_item.strip()})
            message = occurrence_message(dict(note), status, reason, created_at, arrival_time,
                                         affected_item, seller_name)
            return {"event_id": event_id, "duplicate": False, "status": status, "share_text": message}

    def close_driver_trip(self, driver_id, trip_id):
        with self.connection() as db:
            trip = db.execute("SELECT status FROM app.trips WHERE id=%s AND driver_id=%s FOR UPDATE",
                              (trip_id, driver_id)).fetchone()
            if trip and trip["status"] == "closed":
                return {"trip_id": trip_id, "status": "closed", "duplicate": True}
            if not trip or trip["status"] != "released":
                raise BusinessError("Viagem não está ativa para este motorista")
            pending = db.execute("SELECT COUNT(*) AS total FROM app.notes WHERE trip_id=%s AND status='pending'",
                                 (trip_id,)).fetchone()["total"]
            if pending:
                raise BusinessError(f"Existem {pending} NF-e sem baixa ou ocorrência")
            db.execute("UPDATE app.trips SET status='closed',closed_at=now() WHERE id=%s", (trip_id,))
            self._audit(db, f"driver:{driver_id}", "closed", "trip", trip_id, {})
            return {"trip_id": trip_id, "status": "closed", "duplicate": False}
