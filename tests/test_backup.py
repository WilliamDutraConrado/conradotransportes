import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from conrado.backup import BackupError, create_backup, restore_backup, verify_backup


JPEG = b"\xff\xd8\xff" + b"backup-fixture" * 15 + b"\xff\xd9"
EVIDENCE_PATH = "viagens/3/notas/9/evento-1234567890-abcdef012345.jpg"


class Result:
    def __init__(self, one=None, all_rows=None):
        self.one = one
        self.all_rows = all_rows or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.all_rows


class Connection:
    def __init__(self, *, empty=True):
        self.empty = empty

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        if "SELECT storage_path,sha256" in sql:
            return Result(all_rows=[(EVIDENCE_PATH, hashlib.sha256(JPEG).hexdigest())])
        if "to_regclass" in sql:
            return Result(one=(None if self.empty else "app.drivers",))
        if sql.startswith("SELECT EXISTS"):
            return Result(one=(not self.empty,))
        raise AssertionError(sql)


class Storage:
    def __init__(self):
        self.restored = []

    def download(self, path):
        if path != EVIDENCE_PATH:
            raise AssertionError(path)
        return JPEG

    def restore(self, path, content, expected_sha):
        self.restored.append((path, content, expected_sha))

    def exists(self, path):
        return any(item[0] == path for item in self.restored)


def runner(command, **kwargs):
    if "pg_dump" in command[0]:
        output = Path(command[command.index("--file") + 1])
        output.write_bytes(b"PGDMP\x01fixture")
    return SimpleNamespace(returncode=0, stderr=b"")


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.output = Path(self.folder.name) / "backup.conrado"
        self.dsn = "postgresql://user:secret@example.test:5432/recovery?sslmode=require"
        self.password = "SenhaForteDoBackup2026!"
        self.storage = Storage()

    def tearDown(self):
        self.folder.cleanup()

    def test_create_verify_and_restore_without_secret_in_command(self):
        commands = []

        def recording_runner(command, **kwargs):
            commands.append(command)
            return runner(command, **kwargs)

        created = create_backup(
            self.dsn, self.storage, self.output, self.password,
            connect=lambda _dsn: Connection(), runner=recording_runner,
            tool_resolver=lambda name: name,
        )
        manifest = verify_backup(self.output, self.password)
        restored = restore_backup(
            self.dsn, self.storage, self.output, self.password,
            confirm_project_ref="recovery-ref", expected_project_ref="recovery-ref",
            connect=lambda _dsn: Connection(empty=True), runner=recording_runner,
            tool_resolver=lambda name: name,
        )

        self.assertEqual(created["objects"], 1)
        self.assertEqual(len(manifest["objects"]), 1)
        self.assertEqual(restored["objects"], 1)
        self.assertTrue(self.output.read_bytes().startswith(b"CONRADO-BACKUP-V1\n"))
        self.assertTrue(all("secret" not in " ".join(command) for command in commands))
        self.assertTrue(all("postgresql://" not in " ".join(command) for command in commands))

    def test_wrong_password_or_tamper_is_rejected(self):
        create_backup(
            self.dsn, self.storage, self.output, self.password,
            connect=lambda _dsn: Connection(), runner=runner, tool_resolver=lambda name: name,
        )
        with self.assertRaisesRegex(BackupError, "incorreta|corrompido"):
            verify_backup(self.output, "OutraSenhaMuitoForte!")

        damaged = bytearray(self.output.read_bytes())
        damaged[-8] ^= 1
        self.output.write_bytes(damaged)
        with self.assertRaisesRegex(BackupError, "incorreta|corrompido"):
            verify_backup(self.output, self.password)

    def test_restore_requires_exact_project_and_empty_database(self):
        create_backup(
            self.dsn, self.storage, self.output, self.password,
            connect=lambda _dsn: Connection(), runner=runner, tool_resolver=lambda name: name,
        )
        with self.assertRaisesRegex(BackupError, "referência"):
            restore_backup(
                self.dsn, self.storage, self.output, self.password,
                confirm_project_ref="wrong", expected_project_ref="recovery-ref",
                connect=lambda _dsn: Connection(), runner=runner, tool_resolver=lambda name: name,
            )
        with self.assertRaisesRegex(BackupError, "vazio"):
            restore_backup(
                self.dsn, self.storage, self.output, self.password,
                confirm_project_ref="recovery-ref", expected_project_ref="recovery-ref",
                connect=lambda _dsn: Connection(empty=False), runner=runner, tool_resolver=lambda name: name,
            )

    def test_backup_never_overwrites_existing_file(self):
        self.output.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(BackupError, "não haverá sobrescrita"):
            create_backup(
                self.dsn, self.storage, self.output, self.password,
                connect=lambda _dsn: Connection(), runner=runner, tool_resolver=lambda name: name,
            )


if __name__ == "__main__":
    unittest.main()
