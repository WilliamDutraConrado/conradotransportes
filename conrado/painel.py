"""Painel local de conferência de NF-e; não persiste documentos."""

from __future__ import annotations

import base64
import binascii
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from conrado.importer import NFeError, parse_nfe

HERE = Path(__file__).resolve().parent.parent
UI = HERE / "web" / "index.html"
BRAND_IMAGE = HERE / "web" / "assets" / "emblema-dutralog.webp"
DEFAULT_REGEX = (
    r"\b(?:PLACA|VE[IÍ]CULO)\s*[:=-]?\s*"
    r"([A-Z]{3}[0-9][A-Z0-9][0-9]{2})\b"
)
MAX_REQUEST = 15_000_000
MAX_FILES = 100


def inspect_uploads(items: list, regex: str) -> dict:
    import re

    compiled = re.compile(regex)
    if compiled.groups != 1 or len(regex) > 300:
        raise ValueError("A regra de placa precisa ter um único grupo de captura")

    cargas: dict[str, dict] = {}
    errors: list[dict] = []
    seen: set[str] = set()
    for item in items:
        filename = str(item.get("name", "arquivo"))[:120]
        try:
            raw = base64.b64decode(item["base64"], validate=True)
            nota = parse_nfe(raw, regex)
            if nota["chave"] in seen:
                raise NFeError("Nota duplicada neste lote")
            seen.add(nota["chave"])
            placa = nota["placa"] or "SEM PLACA"
            grupo = cargas.setdefault(placa, {"placa": placa, "notas": [], "paradas": {}, "peso_total_kg": 0, "volumes": 0})
            grupo["notas"].append(nota)
            grupo["peso_total_kg"] += nota["peso_bruto_kg"]
            grupo["volumes"] += nota["volumes"]
            parada_id = nota["destinatario_id"] + "|" + nota["endereco"].casefold()
            parada = grupo["paradas"].setdefault(parada_id, {
                "cliente": nota["destinatario"], "endereco": nota["endereco"], "notas": []
            })
            parada["notas"].append(nota["numero"])
        except (KeyError, TypeError, ValueError, NFeError, binascii.Error) as exc:
            errors.append({"arquivo": filename, "erro": str(exc)})

    result = []
    for grupo in sorted(cargas.values(), key=lambda g: g["placa"]):
        grupo["paradas"] = list(grupo["paradas"].values())
        grupo["peso_total_kg"] = round(grupo["peso_total_kg"], 3)
        result.append(grupo)
    return {"cargas": result, "erros": errors, "total_notas": len(seen)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/assets/emblema-dutralog.webp":
            self._respond(200, BRAND_IMAGE.read_bytes(), "image/webp")
            return
        if path != "/":
            self.send_error(404)
            return
        self._respond(200, UI.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/api/preview":
            self.send_error(404)
            return
        # Bind local, exigir JSON e rejeitar Origin remoto para reduzir risco de CSRF.
        if self.headers.get("Origin") not in (None, "http://127.0.0.1:8765", "http://localhost:8765"):
            self._json(403, {"erro": "Origem não permitida"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"erro": "Envie application/json"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_REQUEST:
                raise ValueError("Lote vazio ou muito grande (limite de 15 MB)")
            body = json.loads(self.rfile.read(size))
            items = body.get("arquivos")
            if not isinstance(items, list) or len(items) > MAX_FILES:
                raise ValueError("Limite de 100 arquivos por lote")
            result = inspect_uploads(items, body.get("placa_regex", DEFAULT_REGEX))
            self._json(200, result)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"erro": str(exc)})

    def _json(self, status: int, result: dict):
        self._respond(status, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _respond(self, status: int, content: bytes, mime: str):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Painel local: http://127.0.0.1:8765")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
