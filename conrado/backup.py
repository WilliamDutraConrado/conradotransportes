"""Backup cifrado e restauração isolada do PostgreSQL e das evidências privadas.

O arquivo produzido nunca contém a senha do banco. A restauração recusa banco
com dados e exige a referência exata do projeto de recuperação.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from conrado.config import ConfigError, PROJECT_ROOT, load_env_file
from conrado.supabase_storage import StorageError, SupabaseStorage

MAGIC = b"CONRADO-BACKUP-V1\n"
KDF_ITERATIONS = 1_200_000
MAX_BACKUP_BYTES = 512 * 1024 * 1024
TABLES = (
    "drivers", "vehicles", "trips", "notes", "driver_auth", "driver_sessions",
    "admin_users", "admin_sessions", "login_limits", "delivery_starts", "delivery_events",
    "evidence", "audit",
)


class BackupError(RuntimeError):
    """Falha sanitizada da rotina de backup ou restauração."""


def _fernet(passphrase: str, salt: bytes) -> Fernet:
    if len(passphrase) < 16:
        raise BackupError("A senha do backup precisa ter pelo menos 16 caracteres")
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS,
    ).derive(passphrase.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def _postgres_environment(dsn: str) -> dict[str, str]:
    parsed = urlsplit(dsn)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname \
            or not parsed.username or not parsed.password or not parsed.path.strip("/"):
        raise BackupError("DATABASE_URL precisa ser uma URL PostgreSQL completa")
    query = parse_qs(parsed.query)
    environment = os.environ.copy()
    environment.update({
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": unquote(parsed.path.lstrip("/")),
        "PGUSER": unquote(parsed.username),
        "PGPASSWORD": unquote(parsed.password),
        "PGSSLMODE": query.get("sslmode", ["require"])[0],
    })
    return environment


def _run_tool(command: list[str], environment: dict[str, str], *, runner=subprocess.run) -> None:
    try:
        result = runner(
            command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:
        raise BackupError("Não foi possível iniciar a ferramenta PostgreSQL") from exc
    if result.returncode:
        raise BackupError("A ferramenta PostgreSQL não concluiu a operação")


def _connect_default(dsn: str):
    try:
        import psycopg
    except ImportError as exc:
        raise BackupError("Dependência PostgreSQL não instalada") from exc
    return psycopg.connect(dsn, connect_timeout=10)


def _evidence_rows(dsn: str, *, connect=None) -> list[tuple[str, str]]:
    connect = connect or _connect_default
    try:
        with connect(dsn) as db:
            rows = db.execute("SELECT storage_path,sha256 FROM app.evidence ORDER BY id").fetchall()
    except Exception as exc:
        raise BackupError("Não foi possível enumerar as evidências privadas") from exc
    return [(str(row[0]), str(row[1])) for row in rows]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_output(output: Path) -> Path:
    target = output.resolve()
    if PROJECT_ROOT == target or PROJECT_ROOT in target.parents:
        raise BackupError("Salve o backup fora do repositório do projeto")
    if not target.parent.is_dir():
        raise BackupError("A pasta de destino do backup não existe")
    if target.exists():
        raise BackupError("O arquivo de backup já existe; não haverá sobrescrita")
    return target


def create_backup(dsn: str, storage: SupabaseStorage, output: Path, passphrase: str, *,
                  connect=None, runner=subprocess.run, tool_resolver=shutil.which) -> dict:
    """Cria pacote cifrado com dump PostgreSQL, objetos privados e manifesto de hashes."""
    target = _validate_output(output)
    pg_dump = tool_resolver("pg_dump")
    if not pg_dump:
        raise BackupError("pg_dump não está instalado nesta máquina")
    environment = _postgres_environment(dsn)
    salt = os.urandom(16)

    with tempfile.TemporaryDirectory(prefix="conrado-backup-") as temporary:
        folder = Path(temporary)
        database_dump = folder / "database.dump"
        plain_archive = folder / "backup.zip"
        _run_tool([
            pg_dump, "--format=custom", "--no-owner", "--no-acl", "--file", str(database_dump),
        ], environment, runner=runner)
        if not database_dump.is_file():
            raise BackupError("pg_dump não produziu o arquivo esperado")

        objects = []
        total_size = database_dump.stat().st_size
        with zipfile.ZipFile(plain_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(database_dump, "database.dump")
            for storage_path, expected_sha in _evidence_rows(dsn, connect=connect):
                content = storage.download(storage_path)
                actual_sha = hashlib.sha256(content).hexdigest()
                if actual_sha != expected_sha:
                    raise BackupError("Uma evidência falhou na verificação antes do backup")
                total_size += len(content)
                if total_size > MAX_BACKUP_BYTES:
                    raise BackupError("O backup excede 512 MB; use rotina de armazenamento em escala")
                archive_name = "storage/" + storage_path
                archive.writestr(archive_name, content)
                objects.append({"path": storage_path, "sha256": actual_sha, "size": len(content)})
            manifest = {
                "format": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "database": {"file": "database.dump", "sha256": _hash_file(database_dump),
                             "size": database_dump.stat().st_size},
                "objects": objects,
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        plain = plain_archive.read_bytes()
        encrypted = _fernet(passphrase, salt).encrypt(plain)
        with target.open("xb") as stream:
            stream.write(MAGIC)
            stream.write(base64.urlsafe_b64encode(salt) + b"\n")
            stream.write(encrypted)
    return {"path": str(target), "objects": len(objects), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def _decrypt_backup(path: Path, passphrase: str) -> bytes:
    try:
        raw = path.read_bytes()
        if not raw.startswith(MAGIC):
            raise BackupError("Formato de backup desconhecido")
        salt_line, encrypted = raw[len(MAGIC):].split(b"\n", 1)
        salt = base64.urlsafe_b64decode(salt_line)
        if len(salt) != 16:
            raise BackupError("Cabeçalho de backup inválido")
        plain = _fernet(passphrase, salt).decrypt(encrypted)
    except (OSError, ValueError, InvalidToken) as exc:
        raise BackupError("Senha incorreta ou backup corrompido") from exc
    if len(plain) > MAX_BACKUP_BYTES:
        raise BackupError("O backup excede o limite seguro desta rotina")
    return plain


def _safe_archive_name(name: str) -> bool:
    value = PurePosixPath(name)
    return not value.is_absolute() and ".." not in value.parts and "\\" not in name


def verify_backup(path: Path, passphrase: str) -> dict:
    plain = _decrypt_backup(path, passphrase)
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(plain), "r") as archive:
            if any(not _safe_archive_name(name) for name in archive.namelist()):
                raise BackupError("O backup contém caminho inválido")
            if sum(item.file_size for item in archive.infolist()) > MAX_BACKUP_BYTES:
                raise BackupError("O conteúdo expandido do backup excede o limite seguro")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != 1:
                raise BackupError("Versão de backup incompatível")
            database = archive.read(manifest["database"]["file"])
            if hashlib.sha256(database).hexdigest() != manifest["database"]["sha256"]:
                raise BackupError("O dump do banco falhou na verificação")
            for item in manifest["objects"]:
                content = archive.read("storage/" + item["path"])
                if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise BackupError("Uma evidência falhou na verificação do backup")
    except (KeyError, TypeError, ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise BackupError("Manifesto de backup inválido") from exc
    return manifest


def _database_is_empty(dsn: str, *, connect=None) -> bool:
    connect = connect or _connect_default
    try:
        with connect(dsn) as db:
            exists = db.execute("SELECT to_regclass('app.drivers')").fetchone()[0]
            if not exists:
                return True
            query = "SELECT " + " OR ".join(
                f"EXISTS (SELECT 1 FROM app.{table} LIMIT 1)" for table in TABLES
            )
            has_data = bool(db.execute(query).fetchone()[0])
    except Exception as exc:
        raise BackupError("Não foi possível verificar se o banco de recuperação está vazio") from exc
    return not has_data


def restore_backup(dsn: str, storage: SupabaseStorage, backup_path: Path, passphrase: str, *,
                   confirm_project_ref: str, expected_project_ref: str, connect=None,
                   runner=subprocess.run, tool_resolver=shutil.which) -> dict:
    """Restaura somente em projeto vazio e explicitamente confirmado."""
    if not expected_project_ref or confirm_project_ref != expected_project_ref:
        raise BackupError("Confirme exatamente a referência do projeto de recuperação")
    if not _database_is_empty(dsn, connect=connect):
        raise BackupError("A restauração só é permitida em banco de recuperação vazio")
    pg_restore = tool_resolver("pg_restore")
    if not pg_restore:
        raise BackupError("pg_restore não está instalado nesta máquina")
    manifest = verify_backup(backup_path, passphrase)
    environment = _postgres_environment(dsn)
    plain = _decrypt_backup(backup_path, passphrase)

    with tempfile.TemporaryDirectory(prefix="conrado-restore-") as temporary:
        folder = Path(temporary)
        with zipfile.ZipFile(__import__("io").BytesIO(plain), "r") as archive:
            dump_path = folder / "database.dump"
            dump_path.write_bytes(archive.read("database.dump"))
            _run_tool([
                pg_restore, "--clean", "--if-exists", "--no-owner", "--no-acl", "--dbname",
                environment["PGDATABASE"], str(dump_path),
            ], environment, runner=runner)
            restored = 0
            for item in manifest["objects"]:
                content = archive.read("storage/" + item["path"])
                storage.restore(item["path"], content, item["sha256"])
                if not storage.exists(item["path"]):
                    raise BackupError("A restauração de uma evidência não foi confirmada")
                restored += 1
    return {"database": True, "objects": restored}


def _project_ref_from_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    suffix = ".supabase.co"
    return host[:-len(suffix)] if host.endswith(suffix) else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup cifrado do DutraTracking")
    subparsers = parser.add_subparsers(dest="action", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("output", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("backup", type=Path)
    restore = subparsers.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--confirm-project-ref", required=True)
    args = parser.parse_args()
    try:
        load_env_file()
        password = getpass.getpass("Senha do backup (16+ caracteres): ")
        if args.action == "create":
            confirmation = getpass.getpass("Confirme a senha do backup: ")
            if password != confirmation:
                raise BackupError("As senhas do backup não coincidem")
            result = create_backup(
                os.environ.get("DATABASE_URL", ""), SupabaseStorage.from_environment(),
                args.output, password,
            )
            print(f"Backup cifrado criado e verificado: {result['objects']} objeto(s).")
        elif args.action == "verify":
            manifest = verify_backup(args.backup, password)
            print(f"Backup íntegro: {len(manifest['objects'])} objeto(s).")
        else:
            expected = os.environ.get("SUPABASE_PROJECT_REF", "") or _project_ref_from_url(
                os.environ.get("SUPABASE_URL", "")
            )
            result = restore_backup(
                os.environ.get("DATABASE_URL", ""), SupabaseStorage.from_environment(),
                args.backup, password, confirm_project_ref=args.confirm_project_ref,
                expected_project_ref=expected,
            )
            print(f"Restauração isolada confirmada: {result['objects']} objeto(s).")
    except (BackupError, ConfigError, StorageError, OSError):
        print("Rotina de backup pendente: operação interrompida com segurança.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
