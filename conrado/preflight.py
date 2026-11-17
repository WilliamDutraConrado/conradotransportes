"""Verificação somente-leitura da infraestrutura do piloto.

Uso: python -m conrado.preflight
Não cria tabelas, contas, buckets ou cobranças e nunca imprime segredos.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

TABLES = (
    "drivers", "vehicles", "trips", "notes", "driver_auth", "driver_sessions",
    "delivery_events", "evidence", "audit",
)


def inspect_database(dsn: str, *, connect=None) -> list[str]:
    if not dsn:
        return ["DATABASE_URL não configurada"]
    if connect is None:
        try:
            import psycopg
        except ImportError:
            return ["Dependência psycopg não instalada"]
        connect = psycopg.connect
    try:
        with connect(dsn, connect_timeout=10) as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = %s", ("app",))
                found = {row[0] for row in cursor.fetchall()}
                missing = set(TABLES) - found
                cursor.execute("SELECT has_schema_privilege('anon', 'app', 'USAGE')")
                anon_access = bool(cursor.fetchone()[0])
        issues = ["Tabelas ausentes no schema privado: " + ", ".join(sorted(missing))] if missing else []
        if anon_access:
            issues.append("O papel anon possui acesso ao schema privado app")
        return issues
    except Exception:
        # Mensagens do driver podem conter URL, usuário ou senha; não as exibir.
        return ["Falha na conexão ou consulta ao banco; confira os segredos no provedor"]


def inspect_storage(url: str, key: str, *, opener=None) -> list[str]:
    if not url or not key:
        return ["SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY não configurada"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ["SUPABASE_URL deve ser HTTPS e não conter credenciais"]
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url.rstrip("/") + "/storage/v1/bucket/canhotos", headers={
        "apikey": key, "Authorization": "Bearer " + key,
    })
    try:
        with opener(req, timeout=10) as response:
            bucket = json.load(response)
        if bucket.get("public") is not False:
            return ["Bucket canhotos não está privado"]
        if bucket.get("id") != "canhotos":
            return ["Bucket canhotos não encontrado"]
        return []
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
        return ["Não foi possível consultar o bucket privado canhotos"]


def main() -> int:
    issues = inspect_database(os.environ.get("DATABASE_URL", ""))
    issues += inspect_storage(os.environ.get("SUPABASE_URL", ""),
                              os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
    if issues:
        print("Pré-verificação pendente:")
        for issue in issues:
            print("- " + issue)
        return 1
    print("Pré-verificação aprovada: tabelas e bucket privado disponíveis.")
    print("Ainda é necessário executar testes reais de ponta a ponta antes do deploy público.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
