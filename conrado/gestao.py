"""Servidor local autenticado para o cadastro e a preparação de viagens."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from conrado.importer import NFeError, parse_nfe
from conrado.painel import DEFAULT_REGEX
from conrado.store import BusinessError, Store

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 15_000_000


class Server(ThreadingHTTPServer):
    def __init__(self, address, token: str, database: Path):
        super().__init__(address, Handler)
        self.token = token
        if os.environ.get("CONRADO_BACKEND", "sqlite") == "postgres":
            from conrado.postgres_store import PostgresStore
            from conrado.supabase_storage import SupabaseStorage
            dsn = os.environ.get("DATABASE_URL", "")
            if not dsn:
                raise RuntimeError("DATABASE_URL não configurada para PostgreSQL")
            self.db = PostgresStore(dsn, SupabaseStorage.from_environment())
        else:
            self.db = Store(database)


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, payload: bytes, content_type: str):
        self.send_response(status)
        for key, value in (
            ("Content-Type", content_type), ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
            ("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'"),
        ):
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: dict | list):
        self._respond(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _allowed(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        provided = authorization[7:] if authorization.startswith("Bearer ") else ""
        return hmac.compare_digest(provided, self.server.token)

    def _check_origin(self) -> bool:
        return self.headers.get("Origin") in (None, "http://127.0.0.1:8766", "http://localhost:8766")

    def do_GET(self):
        if self.path == "/":
            self._respond(200, (ROOT / "web" / "gestao.html").read_bytes(), "text/html; charset=utf-8")
            return
        if self.path == "/motorista":
            self._respond(200, (ROOT / "web" / "motorista.html").read_bytes(), "text/html; charset=utf-8")
            return
        if self.path == "/api/driver/trip":
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            driver_id = self.server.db.driver_by_token(token)
            if not driver_id:
                self._json(401, {"erro": "Sessão do motorista inválida ou expirada"})
            else:
                self._json(200, {"trip": self.server.db.driver_trip(driver_id)})
            return
        if not self._allowed():
            self._json(401, {"erro": "Chave administrativa inválida"})
            return
        if self.path == "/api/drivers":
            self._json(200, self.server.db.drivers())
        elif self.path == "/api/trips":
            self._json(200, self.server.db.trips())
        elif self.path == "/api/summary":
            self._json(200, self.server.db.summary())
        elif (match := re.fullmatch(r"/api/trips/(\d+)/detail", self.path)):
            detail = self.server.db.trip_detail(int(match.group(1)))
            if detail is None:
                self._json(404, {"erro": "Viagem não encontrada"})
            else:
                self._json(200, detail)
        else:
            self._json(404, {"erro": "Recurso não encontrado"})

    def do_POST(self):
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
            if self.path == "/api/driver/login":
                result = {"token": self.server.db.authenticate_driver(body["plate"], body["password"])}
            elif (match := re.fullmatch(r"/api/driver/notes/(\d+)/status", self.path)):
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                driver_id = self.server.db.driver_by_token(token)
                if not driver_id:
                    self._json(401, {"erro": "Sessão do motorista inválida"})
                    return
                result = self.server.db.record_delivery(driver_id, int(match.group(1)),
                    body["event_id"], body["status"], body.get("reason", ""), body.get("photo_base64"))
            elif (match := re.fullmatch(r"/api/driver/trips/(\d+)/close", self.path)):
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                driver_id = self.server.db.driver_by_token(token)
                if not driver_id:
                    self._json(401, {"erro": "Sessão do motorista inválida"})
                    return
                result = self.server.db.close_driver_trip(driver_id, int(match.group(1)))
            elif not self._allowed():
                self._json(401, {"erro": "Chave administrativa inválida"})
                return
            elif (match := re.fullmatch(r"/api/drivers/(\d+)/password", self.path)):
                self.server.db.set_driver_password(int(match.group(1)), body["password"])
                result = {"updated": True}
            elif self.path == "/api/drivers":
                result = self.server.db.create_driver(body["name"], body["plate"], body["vehicle_type"])
            elif self.path == "/api/trips":
                result = self.server.db.create_trip(int(body["vehicle_id"]), body["label"])
            elif (match := re.fullmatch(r"/api/trips/(\d+)/import", self.path)):
                items = body["arquivos"]
                if not isinstance(items, list) or len(items) > 100:
                    raise BusinessError("O lote deve conter no máximo 100 XMLs")
                notes = []
                for item in items:
                    try:
                        raw = base64.b64decode(item["base64"], validate=True)
                        notes.append(parse_nfe(raw, body.get("placa_regex", DEFAULT_REGEX)))
                    except (NFeError, binascii.Error, ValueError) as exc:
                        raise BusinessError(f"Arquivo {str(item.get('name', 'XML'))[:60]}: {exc}") from exc
                result = self.server.db.import_notes(int(match.group(1)), notes)
            elif (match := re.fullmatch(r"/api/trips/(\d+)/release", self.path)):
                result = self.server.db.release_trip(int(match.group(1)))
            else:
                self._json(404, {"erro": "Recurso não encontrado"})
                return
            self._json(200, result)
        except (BusinessError, NFeError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._json(400, {"erro": str(exc)})


def main():
    token = os.environ.get("CONRADO_ADMIN_TOKEN", "")
    if len(token) < 20:
        raise SystemExit("Defina CONRADO_ADMIN_TOKEN com pelo menos 20 caracteres aleatórios")
    db_path = Path(os.environ.get("CONRADO_DB_PATH", "conrado_local.sqlite"))
    server = Server(("127.0.0.1", 8766), token, db_path)
    print("Gestão local disponível em http://127.0.0.1:8766", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
