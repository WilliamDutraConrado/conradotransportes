"""Criação ou redefinição manual de administrador com senha oculta."""

from __future__ import annotations

import getpass
import os
import sys

from conrado.config import ConfigError, load_env_file
from conrado.postgres_store import PostgresStore
from conrado.store import BusinessError
from conrado.supabase_storage import SupabaseStorage


def create_admin_account(store, username: str, password: str, confirmation: str) -> dict:
    if password != confirmation:
        raise BusinessError("As senhas não coincidem")
    return store.create_admin(username, password, "admin")


def main() -> int:
    username = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        load_env_file()
        password = getpass.getpass("Senha administrativa (14+ caracteres): ")
        confirmation = getpass.getpass("Confirme a senha: ")
        store = PostgresStore(
            os.environ.get("DATABASE_URL", ""), SupabaseStorage.from_environment()
        )
        result = create_admin_account(store, username, password, confirmation)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, (ConfigError, BusinessError)) else "falha segura de configuração"
        print("Conta administrativa pendente: " + detail)
        return 1
    print(f"Conta administrativa preparada para o usuário {result['username']}.")
    print("Sessões anteriores desse usuário foram revogadas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
