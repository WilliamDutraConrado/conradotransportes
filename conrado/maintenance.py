"""Retenção configurável e manutenção periódica sem exposição de dados."""

from __future__ import annotations

import argparse
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from conrado.config import ConfigError, load_env_file


@dataclass(frozen=True)
class RetentionSettings:
    enabled: bool = False
    evidence_days: int = 180
    audit_days: int = 730
    interval_hours: int = 24


def _bounded_int(environment, name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(environment.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} precisa ser um número inteiro") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} precisa estar entre {minimum} e {maximum}")
    return value


def retention_settings(environment=None) -> RetentionSettings:
    env = os.environ if environment is None else environment
    enabled = env.get("CONRADO_RETENTION_ENABLED", "0")
    if enabled not in ("0", "1"):
        raise ConfigError("CONRADO_RETENTION_ENABLED deve ser 0 ou 1")
    return RetentionSettings(
        enabled=enabled == "1",
        evidence_days=_bounded_int(env, "CONRADO_EVIDENCE_RETENTION_DAYS", 180, 30, 3650),
        audit_days=_bounded_int(env, "CONRADO_AUDIT_RETENTION_DAYS", 730, 90, 3650),
        interval_hours=_bounded_int(env, "CONRADO_MAINTENANCE_INTERVAL_HOURS", 24, 1, 168),
    )


def run_retention(store, settings: RetentionSettings, *, apply: bool,
                  now_at: datetime | None = None) -> dict:
    moment = now_at or datetime.now(timezone.utc)
    return store.cleanup_retention(
        now_at=moment,
        evidence_before=moment - timedelta(days=settings.evidence_days),
        audit_before=moment - timedelta(days=settings.audit_days),
        apply=apply,
    )


class MaintenanceWorker:
    """Executa retenção em thread dedicada; falhas não derrubam o servidor."""

    def __init__(self, store, settings: RetentionSettings, reporter=None):
        self.store = store
        self.settings = settings
        self.reporter = reporter or (lambda _event: None)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="conrado-retention", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = run_retention(self.store, self.settings, apply=True)
                self.reporter({"level": "info", "event": "retention_completed", "deleted": result["deleted"]})
            except Exception as exc:  # a aplicação continua; o operador recebe apenas o tipo seguro
                self.reporter({"level": "error", "event": "retention_failed", "error_type": type(exc).__name__})
            self._stop.wait(self.settings.interval_hours * 3600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prévia ou aplicação da política de retenção")
    parser.add_argument("mode", choices=("preview", "apply"))
    args = parser.parse_args()
    try:
        load_env_file()
        settings = retention_settings()
        if os.environ.get("CONRADO_BACKEND", "sqlite") == "postgres":
            from conrado.postgres_store import PostgresStore
            from conrado.supabase_storage import SupabaseStorage
            store = PostgresStore(os.environ.get("DATABASE_URL", ""), SupabaseStorage.from_environment())
        else:
            from pathlib import Path
            from conrado.store import Store
            store = Store(Path(os.environ.get("CONRADO_DB_PATH", "conrado_local.sqlite")))
        result = run_retention(store, settings, apply=args.mode == "apply")
    except (ConfigError, RuntimeError, ValueError):
        print("Manutenção não executada: configuração inválida ou serviço indisponível.")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
