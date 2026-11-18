"""Verificação local, sem rede, da configuração preparada para hospedagem."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from conrado.config import ConfigError, PROJECT_ROOT, load_env_file, server_settings
from conrado.maintenance import retention_settings

PWA_FILES = (
    "web/manifest.webmanifest", "web/sw.js", "web/assets/app.css",
    "web/assets/motorista.js", "web/assets/offline-queue.js", "web/assets/icon.svg",
)


def inspect_release_configuration(environment, *, tool_resolver=shutil.which,
                                  project_root: Path = PROJECT_ROOT) -> list[str]:
    issues = []
    try:
        settings = server_settings(environment)
        retention = retention_settings(environment)
    except ConfigError as exc:
        return [str(exc)]
    if settings.bind != "0.0.0.0":
        issues.append("CONRADO_BIND precisa ser 0.0.0.0 na hospedagem")
    if settings.allow_admin_token:
        issues.append("CONRADO_ALLOW_ADMIN_TOKEN precisa ser 0 na hospedagem")
    if not settings.access_log:
        issues.append("CONRADO_ACCESS_LOG precisa ser 1 na hospedagem")
    if not retention.enabled:
        issues.append("CONRADO_RETENTION_ENABLED precisa ser 1 após aprovação dos prazos")
    if len(settings.allowed_origins) != 1 or any(origin.startswith("http://") for origin in settings.allowed_origins):
        issues.append("Configure exatamente uma origem HTTPS de homologação")
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_PROJECT_REF"):
        if not environment.get(name):
            issues.append(f"{name} não configurada")
    if environment.get("SUPABASE_SERVICE_ROLE_KEY") and not environment.get("SUPABASE_SECRET_KEY"):
        issues.append("A chave legada service_role não é aceita como configuração final")
    for tool in ("pg_dump", "pg_restore"):
        if not tool_resolver(tool):
            issues.append(f"{tool} não encontrado")
    for relative in PWA_FILES:
        if not (project_root / relative).is_file():
            issues.append(f"Arquivo PWA ausente: {relative}")
    return issues


def main() -> int:
    try:
        load_env_file()
        issues = inspect_release_configuration(os.environ)
    except ConfigError as exc:
        issues = [str(exc)]
    if issues:
        print("Preparação de hospedagem pendente:")
        for issue in issues:
            print("- " + issue)
        return 1
    print("Configuração local preparada para a homologação hospedada.")
    print("Execute ainda preflight, smoke, E2E, auditoria e ensaio de restauração.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
