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

from conrado.config import ConfigError, load_env_file, supabase_server_key

TABLES = (
    "drivers", "vehicles", "trips", "notes", "driver_auth", "driver_sessions",
    "admin_users", "admin_sessions", "login_limits", "delivery_starts", "delivery_events",
    "evidence", "audit",
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
                cursor.execute("SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = %s", ("app",))
                rows = cursor.fetchall()
                found = {row[0] for row in rows}
                missing = set(TABLES) - found
                without_rls = sorted(row[0] for row in rows if row[0] in TABLES and not row[1])
                cursor.execute("""SELECT has_schema_privilege('anon', 'app', 'USAGE'),
                    has_schema_privilege('authenticated', 'app', 'USAGE')""")
                anon_access, authenticated_access = map(bool, cursor.fetchone())
                cursor.execute("""SELECT column_name,is_nullable FROM information_schema.columns
                    WHERE table_schema='app' AND table_name='admin_users' AND column_name='role'""")
                role_column = cursor.fetchone()
                cursor.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                    WHERE conrelid='app.admin_users'::regclass AND contype='c'""")
                role_constraints = [str(row[0]) for row in cursor.fetchall()]
        issues = ["Tabelas ausentes no schema privado: " + ", ".join(sorted(missing))] if missing else []
        if without_rls:
            issues.append("Tabelas sem RLS: " + ", ".join(without_rls))
        if anon_access:
            issues.append("O papel anon possui acesso ao schema privado app")
        if authenticated_access:
            issues.append("O papel authenticated possui acesso ao schema privado app")
        if not role_column or role_column[1] != "NO":
            issues.append("A coluna obrigatória de perfil administrativo não está configurada")
        joined_constraints = " ".join(role_constraints)
        if not all(role in joined_constraints for role in ("admin", "operator", "auditor")):
            issues.append("A restrição dos perfis administrativos não está configurada")
        return issues
    except Exception:
        # Mensagens do driver podem conter URL, usuário ou senha; não as exibir.
        return ["Falha na conexão ou consulta ao banco; confira os segredos no provedor"]


def inspect_storage(url: str, key: str, *, opener=None) -> list[str]:
    if not url or not key:
        return ["SUPABASE_URL ou SUPABASE_SECRET_KEY não configurada"]
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
        issues = []
        if bucket.get("file_size_limit") != 5 * 1024 * 1024:
            issues.append("Bucket canhotos deve limitar cada arquivo a 5 MB")
        allowed = bucket.get("allowed_mime_types")
        if not isinstance(allowed, list) or set(allowed) != {"image/jpeg", "image/png"}:
            issues.append("Bucket canhotos deve aceitar somente JPEG e PNG")
        return issues
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
        return ["Não foi possível consultar o bucket privado canhotos"]


def main() -> int:
    try:
        load_env_file()
    except ConfigError as exc:
        print("Pré-verificação pendente:")
        print("- " + str(exc))
        return 1
    issues = inspect_database(os.environ.get("DATABASE_URL", ""))
    issues += inspect_storage(os.environ.get("SUPABASE_URL", ""),
                              supabase_server_key())
    if issues:
        print("Pré-verificação pendente:")
        for issue in issues:
            print("- " + issue)
        return 1
    print("Pré-verificação aprovada: tabelas e bucket privado disponíveis.")
    if not os.environ.get("SUPABASE_SECRET_KEY") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        print("Migração pendente: substitua a chave service_role por SUPABASE_SECRET_KEY.")
    print("O deploy público continua bloqueado até concluir as proteções de internet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
