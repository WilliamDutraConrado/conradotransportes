"""Carregamento mínimo de configuração local sem expor segredos.

O arquivo ``.env`` é opcional, ignorado pelo Git e lido apenas pelos pontos de
entrada do backend. Variáveis já definidas no processo sempre têm precedência.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Configuração local inválida, sem incluir o valor sensível na mensagem."""


_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ServerSettings:
    bind: str
    port: int
    allowed_origins: frozenset[str]
    access_log: bool
    allow_admin_token: bool


def server_settings(environment=None) -> ServerSettings:
    """Valida exposição de rede sem incluir valores sensíveis nos erros."""
    env = os.environ if environment is None else environment
    bind = env.get("CONRADO_BIND", "127.0.0.1")
    if bind not in ("127.0.0.1", "0.0.0.0"):
        raise ConfigError("CONRADO_BIND deve ser 127.0.0.1 ou 0.0.0.0")
    try:
        port = int(env.get("PORT", env.get("CONRADO_PORT", "8766")))
    except (TypeError, ValueError) as exc:
        raise ConfigError("PORT precisa ser um número entre 1 e 65535") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("PORT precisa ser um número entre 1 e 65535")

    origins = set()
    for raw in env.get("CONRADO_ALLOWED_ORIGINS", "").split(","):
        value = raw.strip().rstrip("/")
        if not value:
            continue
        parsed = urlsplit(value)
        local_http = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
        if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username \
                or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ConfigError("CONRADO_ALLOWED_ORIGINS contém uma origem inválida")
        origins.add(value)
    if bind == "0.0.0.0" and not origins:
        raise ConfigError("Defina CONRADO_ALLOWED_ORIGINS ao habilitar acesso externo")
    if not origins:
        origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    allow_value = env.get("CONRADO_ALLOW_ADMIN_TOKEN", "0" if bind == "0.0.0.0" else "1")
    if allow_value not in ("0", "1"):
        raise ConfigError("CONRADO_ALLOW_ADMIN_TOKEN deve ser 0 ou 1")
    return ServerSettings(
        bind, port, frozenset(origins), env.get("CONRADO_ACCESS_LOG") == "1", allow_value == "1"
    )


def supabase_server_key(environment=None) -> str:
    """Prefere a chave secreta atual e mantém transição da chave legada."""
    env = os.environ if environment is None else environment
    return env.get("SUPABASE_SECRET_KEY", "") or env.get("SUPABASE_SERVICE_ROLE_KEY", "")


def load_env_file(path: Path | None = None) -> int:
    """Carrega pares simples ``NOME=valor`` sem sobrescrever o ambiente.

    Retorna quantas variáveis novas foram definidas. Os valores nunca são
    impressos e não há expansão de comandos ou de outras variáveis.
    """
    target = path or PROJECT_ROOT / ".env"
    if not target.exists():
        return 0

    loaded = 0
    try:
        lines = target.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ConfigError("Não foi possível ler o arquivo local de configuração") from exc

    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"Linha {number} inválida no arquivo .env")
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if not _NAME.fullmatch(name):
            raise ConfigError(f"Nome inválido na linha {number} do arquivo .env")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if "\x00" in value:
            raise ConfigError(f"Valor inválido na linha {number} do arquivo .env")
        if name not in os.environ:
            os.environ[name] = value
            loaded += 1
    return loaded
