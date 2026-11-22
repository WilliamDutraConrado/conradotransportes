"""Executor restrito das migrações SQL conhecidas do projeto."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from conrado.config import ConfigError, load_env_file

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = {
    "003_persistent_login_limits": (
        ROOT / "infra" / "supabase" / "003_persistent_login_limits.sql", ("login_limits",),
    ),
    "004_admin_auth": (
        ROOT / "infra" / "supabase" / "004_admin_auth.sql", ("admin_users", "admin_sessions"),
    ),
    "005_admin_roles": (
        ROOT / "infra" / "supabase" / "005_admin_roles.sql", ("admin_users",),
    ),
    "006_retention_indexes": (
        ROOT / "infra" / "supabase" / "006_retention_indexes.sql",
        ("audit", "evidence", "delivery_events", "admin_sessions", "driver_sessions"),
    ),
    "007_delivery_workflow": (
        ROOT / "infra" / "supabase" / "007_delivery_workflow.sql",
        ("delivery_starts", "delivery_events"),
    ),
    "008_driver_positions": (
        ROOT / "infra" / "supabase" / "008_driver_positions.sql", ("driver_positions",),
    ),
}


def apply_migration(dsn: str, name: str, *, connect=None) -> None:
    migration = MIGRATIONS.get(name)
    if migration is None:
        raise RuntimeError("Migração não autorizada pelo executor")
    path, tables = migration
    if not dsn:
        raise RuntimeError("DATABASE_URL não configurada")
    if connect is None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Dependência PostgreSQL não instalada") from exc
        connect = psycopg.connect
    sql = path.read_text(encoding="utf-8")
    try:
        with connect(dsn, connect_timeout=10) as db:
            db.execute(sql)
        with connect(dsn, connect_timeout=10) as db:
            verified = []
            for table in tables:
                verified.append(db.execute("""SELECT rowsecurity,
                    NOT has_table_privilege('anon','app.' || tablename,'SELECT'),
                    NOT has_table_privilege('authenticated','app.' || tablename,'SELECT')
                    FROM pg_tables WHERE schemaname='app' AND tablename=%s""", (table,)).fetchone())
    except Exception as exc:
        raise RuntimeError("A migração falhou sem expor detalhes da conexão") from exc
    if len(verified) != len(tables) or any(
        not row or not all(bool(value) for value in row) for row in verified
    ):
        raise RuntimeError("A migração não passou na verificação de segurança")


def main() -> int:
    name = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        load_env_file()
        apply_migration(os.environ.get("DATABASE_URL", ""), name)
    except (ConfigError, RuntimeError, OSError):
        print("Migração pendente: execução ou verificação segura falhou.")
        return 1
    print(f"Migração aprovada: {name} aplicada e verificada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
