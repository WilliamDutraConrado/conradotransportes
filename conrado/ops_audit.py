"""Auditoria agregada e somente leitura do ambiente PostgreSQL/Storage."""

from __future__ import annotations

import os
import sys

from conrado.config import ConfigError, load_env_file
from conrado.supabase_storage import SupabaseStorage

MAX_STORAGE_CHECKS = 500


def audit_integrity(dsn: str, storage, *, connect=None) -> dict:
    if not dsn:
        raise RuntimeError("DATABASE_URL não configurada")
    if connect is None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Dependência PostgreSQL não instalada") from exc
        connect = psycopg.connect
    try:
        with connect(dsn, connect_timeout=10) as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM app.driver_sessions WHERE expires_at <= now()")
                expired_sessions = int(cursor.fetchone()[0])
                cursor.execute("""SELECT COUNT(*) FROM app.notes n
                    WHERE n.status <> 'pending' AND NOT EXISTS (
                        SELECT 1 FROM app.delivery_events e
                        WHERE e.note_id=n.id AND e.status=n.status
                    )""")
                inconsistent_notes = int(cursor.fetchone()[0])
                cursor.execute("""SELECT COUNT(*) FROM app.delivery_events e
                    WHERE e.status='delivered' AND NOT EXISTS (
                        SELECT 1 FROM app.evidence x WHERE x.event_id=e.id
                    )""")
                deliveries_without_evidence = int(cursor.fetchone()[0])
                cursor.execute("SELECT storage_path FROM app.evidence ORDER BY id LIMIT %s",
                               (MAX_STORAGE_CHECKS + 1,))
                paths = [row[0] for row in cursor.fetchall()]
    except Exception as exc:
        raise RuntimeError("Falha segura ao consultar a integridade operacional") from exc

    truncated = len(paths) > MAX_STORAGE_CHECKS
    missing_objects = 0
    try:
        for path in paths[:MAX_STORAGE_CHECKS]:
            if not storage.exists(path):
                missing_objects += 1
    except Exception as exc:
        raise RuntimeError("Falha segura ao conferir os objetos privados") from exc

    issues = []
    if inconsistent_notes:
        issues.append("Há notas baixadas sem evento correspondente")
    if deliveries_without_evidence:
        issues.append("Há entregas concluídas sem evidência vinculada")
    if missing_objects:
        issues.append("Há evidências do banco sem arquivo no bucket")
    if truncated:
        issues.append("A amostra de evidências excedeu o limite da auditoria")
    return {
        "expired_sessions": expired_sessions,
        "inconsistent_notes": inconsistent_notes,
        "deliveries_without_evidence": deliveries_without_evidence,
        "evidence_checked": min(len(paths), MAX_STORAGE_CHECKS),
        "missing_objects": missing_objects,
        "truncated": truncated,
        "issues": issues,
    }


def main() -> int:
    try:
        load_env_file()
        result = audit_integrity(
            os.environ.get("DATABASE_URL", ""), SupabaseStorage.from_environment()
        )
    except (ConfigError, RuntimeError, ValueError):
        print("Auditoria operacional pendente: confira a configuração segura do ambiente.")
        return 1
    print(
        "Auditoria operacional: "
        f"{result['evidence_checked']} evidência(s) conferida(s), "
        f"{result['expired_sessions']} sessão(ões) expirada(s)."
    )
    if result["issues"]:
        for issue in result["issues"]:
            print("- " + issue)
        return 1
    print("Integridade aprovada: nenhuma inconsistência operacional detectada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
