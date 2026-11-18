"""Servidor local autenticado para o cadastro e a preparação de viagens."""

from __future__ import annotations

import base64
import binascii
import csv
import hmac
import io
import json
import os
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from conrado.config import ConfigError, load_env_file, server_settings
from conrado.importer import NFeError, parse_nfe
from conrado.maintenance import MaintenanceWorker, retention_settings
from conrado.painel import DEFAULT_REGEX
from conrado.store import BusinessError, Store
from conrado.version import VERSION

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 15_000_000
LOGIN_WINDOW_SECONDS = 300
STATIC_ROUTES = {
    "/assets/app.css": ("web/assets/app.css", "text/css; charset=utf-8", "public, max-age=3600"),
    "/assets/gestao.js": ("web/assets/gestao.js", "text/javascript; charset=utf-8", "public, max-age=3600"),
    "/assets/motorista.js": ("web/assets/motorista.js", "text/javascript; charset=utf-8", "public, max-age=3600"),
    "/assets/offline-queue.js": ("web/assets/offline-queue.js", "text/javascript; charset=utf-8", "public, max-age=3600"),
    "/assets/icon.svg": ("web/assets/icon.svg", "image/svg+xml", "public, max-age=86400"),
    "/assets/apple-touch-icon.png": ("web/assets/apple-touch-icon.png", "image/png", "public, max-age=86400"),
    "/assets/icon-192.png": ("web/assets/icon-192.png", "image/png", "public, max-age=86400"),
    "/assets/icon-512.png": ("web/assets/icon-512.png", "image/png", "public, max-age=86400"),
    "/assets/icon-maskable-512.png": ("web/assets/icon-maskable-512.png", "image/png", "public, max-age=86400"),
    "/manifest.webmanifest": ("web/manifest.webmanifest", "application/manifest+json; charset=utf-8", "public, max-age=300"),
    "/sw.js": ("web/sw.js", "text/javascript; charset=utf-8", "no-cache"),
}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token: str, database: Path, *, allowed_origins=None, access_log=False,
                 allow_admin_token=True):
        super().__init__(address, Handler)
        self.token = token
        self.allowed_origins = set(allowed_origins or {
            "http://127.0.0.1:8766", "http://localhost:8766",
        })
        self.access_log = access_log
        self.allow_admin_token = allow_admin_token
        if os.environ.get("CONRADO_BACKEND", "sqlite") == "postgres":
            from conrado.postgres_store import PostgresStore
            from conrado.supabase_storage import SupabaseStorage
            dsn = os.environ.get("DATABASE_URL", "")
            if not dsn:
                raise RuntimeError("DATABASE_URL não configurada para PostgreSQL")
            self.db = PostgresStore(dsn, SupabaseStorage.from_environment())
        else:
            self.db = Store(database)
        self.maintenance_worker = None
        maintenance = retention_settings()
        if maintenance.enabled:
            self.maintenance_worker = MaintenanceWorker(self.db, maintenance, self.report_event)
            self.maintenance_worker.start()

    def server_close(self):
        if self.maintenance_worker:
            self.maintenance_worker.stop()
        super().server_close()

    def report_event(self, event: dict) -> None:
        if self.access_log:
            print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
    def login_blocked(self, client: str, plate: str) -> bool:
        return self.db.login_blocked(client, plate)

    def record_login_failure(self, client: str, plate: str) -> None:
        self.db.record_login_failure(client, plate)

    def clear_login_failures(self, client: str, plate: str) -> None:
        self.db.clear_login_failures(client, plate)

    def report_internal_error(self, request_id: str, exc: Exception) -> None:
        self.report_event({
            "level": "error", "event": "request_failed", "request_id": request_id,
            "error_type": type(exc).__name__,
        })

    def handle_error(self, request, client_address):
        self.report_event({"level": "error", "event": "connection_failed"})


class Handler(BaseHTTPRequestHandler):
    server_version = "Conrado"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(30)
        self.request_id = secrets.token_hex(8)
        self.started_at = time.monotonic()

    def _respond(self, status: int, payload: bytes, content_type: str, extra_headers: dict | None = None):
        self.send_response(status)
        headers = dict((
            ("Content-Type", content_type), ("Content-Length", str(len(payload))),
            ("X-Request-ID", self.request_id),
            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
            ("X-Robots-Tag", "noindex, nofollow"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Strict-Transport-Security", "max-age=31536000"),
            ("Permissions-Policy", "camera=(self), geolocation=(), microphone=()"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
             "connect-src 'self'; img-src 'self' blob: data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
        ))
        headers.update(extra_headers or {})
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: dict | list, extra_headers: dict | None = None):
        self._respond(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8",
                      extra_headers)

    @staticmethod
    def _csv_cell(value) -> str:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        text = "" if value is None else str(value)
        if text.startswith(("=", "+", "-", "@", "\t", "\r")):
            text = "'" + text
        return text

    def _csv(self, rows: list[dict], filename: str, columns: tuple[tuple[str, str], ...]):
        output = io.StringIO(newline="")
        writer = csv.writer(output, dialect="excel")
        writer.writerow([label for _key, label in columns])
        for row in rows:
            writer.writerow([self._csv_cell(row.get(key)) for key, _label in columns])
        payload = ("\ufeff" + output.getvalue()).encode("utf-8")
        self._respond(200, payload, "text/csv; charset=utf-8", {
            "Content-Disposition": f'attachment; filename="{filename}"',
        })

    def _admin_context(self) -> dict | None:
        authorization = self.headers.get("Authorization", "")
        provided = authorization[7:] if authorization.startswith("Bearer ") else ""
        if self.server.allow_admin_token and provided and hmac.compare_digest(provided, self.server.token):
            return {"admin_id": None, "username": "legacy", "role": "admin", "actor": "admin:legacy"}
        identity = self.server.db.admin_identity_by_token(provided) if provided else None
        if not identity:
            return None
        return {**identity, "actor": f"admin:{identity['admin_id']}"}

    def _admin_actor(self) -> str | None:
        context = self._admin_context()
        return context["actor"] if context else None

    def _allowed(self) -> bool:
        return self._admin_actor() is not None

    def _check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in self.server.allowed_origins

    def do_GET(self):
        try:
            self._do_GET()
        except (BrokenPipeError, ConnectionError):
            return
        except Exception as exc:
            self.server.report_internal_error(self.request_id, exc)
            try:
                self._json(500, {"erro": "Falha interna", "request_id": self.request_id})
            except (BrokenPipeError, ConnectionError):
                pass

    def _do_GET(self):
        if self.path == "/healthz":
            self._json(200, {"status": "ok", "version": VERSION})
            return
        if self.path == "/readyz":
            try:
                ready = self.server.db.healthcheck()
            except Exception:
                ready = False
            self._json(200 if ready else 503, {"status": "ready" if ready else "unavailable"})
            return
        if self.path == "/version.json":
            self._json(200, {"version": VERSION}, {"Cache-Control": "no-cache"})
            return
        if self.path == "/":
            self._respond(200, (ROOT / "web" / "gestao.html").read_bytes(), "text/html; charset=utf-8")
            return
        if self.path == "/gestao":
            self._respond(200, (ROOT / "web" / "gestao.html").read_bytes(), "text/html; charset=utf-8")
            return
        if self.path == "/motorista":
            self._respond(200, (ROOT / "web" / "motorista.html").read_bytes(), "text/html; charset=utf-8")
            return
        if self.path in STATIC_ROUTES:
            relative, content_type, cache_control = STATIC_ROUTES[self.path]
            headers = {"Cache-Control": cache_control}
            if self.path == "/sw.js":
                headers["Service-Worker-Allowed"] = "/"
            self._respond(200, (ROOT / relative).read_bytes(), content_type, headers)
            return
        if self.path == "/api/driver/trip":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            driver_id = self.server.db.driver_by_token(token)
            if not driver_id:
                self._json(401, {"erro": "Sessão do motorista inválida ou expirada"})
            else:
                self._json(200, {"trip": self.server.db.driver_trip(driver_id)})
            return
        admin_context = self._admin_context()
        if not admin_context:
            self._json(401, {"erro": "Chave administrativa inválida"})
            return
        if self.path == "/api/admin/me":
            self._json(200, {key: admin_context[key] for key in ("admin_id", "username", "role")})
        elif self.path == "/api/admin/diagnostics":
            if admin_context["role"] not in ("admin", "auditor"):
                self._json(403, {"erro": "Perfil sem permissão para diagnóstico"})
            else:
                self._json(200, {"version": VERSION, **self.server.db.diagnostics()})
        elif self.path == "/api/admin/export/audit.csv":
            if admin_context["role"] not in ("admin", "auditor"):
                self._json(403, {"erro": "Perfil sem permissão para exportar auditoria"})
            else:
                self._csv(self.server.db.audit_export(), "auditoria-conrado.csv", (
                    ("happened_at", "Data/hora UTC"), ("actor", "Responsável"),
                    ("event", "Evento"), ("object_type", "Tipo"),
                    ("object_id", "Identificador"), ("payload", "Detalhes"),
                ))
        elif self.path == "/api/admin/export/occurrences.csv":
            self._csv(self.server.db.occurrence_export(), "ocorrencias-conrado.csv", (
                ("created_at", "Data/hora UTC"), ("status", "Situação"),
                ("invoice", "NF-e"), ("recipient", "Cliente"), ("city", "Cidade"),
                ("trip", "Viagem"), ("plate", "Placa"), ("driver", "Motorista"),
                ("arrival_time", "Chegada"), ("reason", "Motivo"),
                ("affected_item", "Item afetado"), ("seller_name", "Vendedor"),
                ("has_evidence", "Possui evidência"),
            ))
        elif self.path == "/api/admin/users":
            if admin_context["role"] != "admin":
                self._json(403, {"erro": "Perfil sem permissão para gerenciar administradores"})
            else:
                self._json(200, self.server.db.admin_users())
        elif self.path == "/api/drivers":
            self._json(200, self.server.db.drivers())
        elif self.path == "/api/trips":
            self._json(200, self.server.db.trips())
        elif self.path == "/api/summary":
            self._json(200, self.server.db.summary())
        elif self.path == "/api/productivity":
            self._json(200, self.server.db.driver_productivity())
        elif (match := re.fullmatch(r"/api/evidence/([A-Za-z0-9-]{12,90})/access", self.path)):
            access = self.server.db.evidence_access(match.group(1))
            if access is None:
                self._json(404, {"erro": "Evidência não encontrada"})
            else:
                self._json(200, access)
        elif (match := re.fullmatch(r"/api/trips/(\d+)/detail", self.path)):
            detail = self.server.db.trip_detail(int(match.group(1)))
            if detail is None:
                self._json(404, {"erro": "Viagem não encontrada"})
            else:
                self._json(200, detail)
        else:
            self._json(404, {"erro": "Recurso não encontrado"})

    def do_POST(self):
        try:
            self._do_POST()
        except (BrokenPipeError, ConnectionError):
            return
        except Exception as exc:
            self.server.report_internal_error(self.request_id, exc)
            try:
                self._json(500, {"erro": "Falha interna", "request_id": self.request_id})
            except (BrokenPipeError, ConnectionError):
                pass

    def _do_POST(self):
        if not self._check_origin():
            self._json(403, {"erro": "Origem não permitida"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"erro": "Conteúdo precisa ser JSON"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise BusinessError("Lote inválido ou maior que 15 MB")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise BusinessError("Formato inválido")
            if self.path == "/api/admin/login":
                client = self.client_address[0]
                username = str(body.get("username", ""))
                rate_key = "admin:" + username
                if self.server.login_blocked(client, rate_key):
                    self._json(429, {"erro": "Muitas tentativas. Aguarde cinco minutos."},
                               {"Retry-After": str(LOGIN_WINDOW_SECONDS)})
                    return
                try:
                    token = self.server.db.authenticate_admin(username, body["password"])
                except BusinessError:
                    self.server.record_login_failure(client, rate_key)
                    self._json(401, {"erro": "Credenciais inválidas"})
                    return
                self.server.clear_login_failures(client, rate_key)
                identity = self.server.db.admin_identity_by_token(token)
                result = {"token": token, "user": identity}
            elif self.path == "/api/admin/logout":
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                self.server.db.revoke_admin_session(token)
                result = {"logged_out": True}
            elif self.path == "/api/driver/login":
                client = self.client_address[0]
                plate = str(body.get("plate", ""))
                if self.server.login_blocked(client, plate):
                    self._json(429, {"erro": "Muitas tentativas. Aguarde cinco minutos."},
                               {"Retry-After": str(LOGIN_WINDOW_SECONDS)})
                    return
                try:
                    token = self.server.db.authenticate_driver(body["plate"], body["password"])
                except BusinessError:
                    self.server.record_login_failure(client, plate)
                    self._json(401, {"erro": "Credenciais inválidas"})
                    return
                self.server.clear_login_failures(client, plate)
                result = {"token": token}
            elif self.path == "/api/driver/logout":
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                self.server.db.revoke_driver_session(token)
                result = {"logged_out": True}
            elif (match := re.fullmatch(r"/api/driver/notes/(\d+)/status", self.path)):
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                driver_id = self.server.db.driver_by_token(token)
                if not driver_id:
                    self._json(401, {"erro": "Sessão do motorista inválida"})
                    return
                result = self.server.db.record_delivery(driver_id, int(match.group(1)),
                    body["event_id"], body["status"], body.get("reason", ""), body.get("photo_base64"),
                    body.get("arrival_time", ""), body.get("affected_item", ""), body.get("seller_name", ""))
            elif (match := re.fullmatch(r"/api/driver/trips/(\d+)/close", self.path)):
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                driver_id = self.server.db.driver_by_token(token)
                if not driver_id:
                    self._json(401, {"erro": "Sessão do motorista inválida"})
                    return
                result = self.server.db.close_driver_trip(driver_id, int(match.group(1)))
            elif not (admin_context := self._admin_context()):
                self._json(401, {"erro": "Chave administrativa inválida"})
                return
            elif self.path == "/api/admin/users":
                if admin_context["role"] != "admin":
                    self._json(403, {"erro": "Perfil sem permissão para gerenciar administradores"})
                    return
                result = self.server.db.create_admin(
                    body["username"], body["password"], body.get("role", "operator"),
                    actor=admin_context["actor"],
                )
            elif (match := re.fullmatch(r"/api/admin/users/(\d+)/access", self.path)):
                if admin_context["role"] != "admin":
                    self._json(403, {"erro": "Perfil sem permissão para gerenciar administradores"})
                    return
                result = self.server.db.set_admin_access(
                    int(match.group(1)), body["role"], body["active"],
                    actor=admin_context["actor"], current_admin_id=admin_context["admin_id"],
                )
            elif (match := re.fullmatch(r"/api/drivers/(\d+)/password", self.path)):
                if admin_context["role"] != "admin":
                    self._json(403, {"erro": "Apenas administradores podem redefinir senhas"})
                    return
                self.server.db.set_driver_password(
                    int(match.group(1)), body["password"], actor=admin_context["actor"],
                )
                result = {"updated": True}
            elif self.path == "/api/drivers":
                if admin_context["role"] not in ("admin", "operator"):
                    self._json(403, {"erro": "Perfil somente leitura"})
                    return
                result = self.server.db.create_driver(
                    body["name"], body["plate"], body["vehicle_type"], actor=admin_context["actor"],
                )
            elif self.path == "/api/trips":
                if admin_context["role"] not in ("admin", "operator"):
                    self._json(403, {"erro": "Perfil somente leitura"})
                    return
                result = self.server.db.create_trip(
                    int(body["vehicle_id"]), body["label"], actor=admin_context["actor"],
                )
            elif (match := re.fullmatch(r"/api/trips/(\d+)/import", self.path)):
                if admin_context["role"] not in ("admin", "operator"):
                    self._json(403, {"erro": "Perfil somente leitura"})
                    return
                items = body["arquivos"]
                if not isinstance(items, list) or len(items) > 100:
                    raise BusinessError("O lote deve conter no máximo 100 XMLs")
                notes = []
                for item in items:
                    try:
                        raw = base64.b64decode(item["base64"], validate=True)
                        # A API utiliza somente a expressão revisada do servidor; não
                        # executa regex arbitrária enviada pelo navegador.
                        notes.append(parse_nfe(raw, DEFAULT_REGEX))
                    except (NFeError, binascii.Error, ValueError) as exc:
                        raise BusinessError(f"Arquivo {str(item.get('name', 'XML'))[:60]}: {exc}") from exc
                result = self.server.db.import_notes(
                    int(match.group(1)), notes, actor=admin_context["actor"],
                )
            elif (match := re.fullmatch(r"/api/trips/(\d+)/release", self.path)):
                if admin_context["role"] not in ("admin", "operator"):
                    self._json(403, {"erro": "Perfil somente leitura"})
                    return
                result = self.server.db.release_trip(
                    int(match.group(1)), actor=admin_context["actor"],
                )
            else:
                self._json(404, {"erro": "Recurso não encontrado"})
                return
            self._json(200, result)
        except (BusinessError, NFeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._json(400, {"erro": str(exc)})

    def log_message(self, format, *args):
        if not self.server.access_log:
            return
        status = str(args[1]) if len(args) > 1 else "unknown"
        path = self.path.split("?", 1)[0]
        path = re.sub(r"/api/(driver/notes|driver/trips|drivers|trips|admin/users)/[^/]+", r"/api/\1/{id}", path)
        path = re.sub(r"/api/evidence/[^/]+/access", "/api/evidence/{event}/access", path)
        print(json.dumps({
            "level": "info", "event": "http_request", "request_id": self.request_id,
            "method": self.command, "route": path, "status": status,
            "duration_ms": round((time.monotonic() - self.started_at) * 1000, 1),
        }, sort_keys=True), flush=True)


def main():
    try:
        load_env_file()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        settings = server_settings()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    token = os.environ.get("CONRADO_ADMIN_TOKEN", "")
    if settings.allow_admin_token and len(token) < 32:
        raise SystemExit("Defina CONRADO_ADMIN_TOKEN com pelo menos 32 caracteres aleatórios")
    db_path = Path(os.environ.get("CONRADO_DB_PATH", "conrado_local.sqlite"))
    server = Server((settings.bind, settings.port), token, db_path,
                    allowed_origins=settings.allowed_origins,
                    access_log=settings.access_log,
                    allow_admin_token=settings.allow_admin_token)
    print(f"Gestão disponível em {settings.bind}:{settings.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
