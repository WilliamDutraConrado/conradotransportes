"""Adaptador privado de fotografias para Supabase Storage.

Uso exclusivo no backend; nunca envie a chave de serviço ao aplicativo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class StorageError(RuntimeError):
    """Falha segura de armazenamento remoto."""


@dataclass(frozen=True)
class UploadedEvidence:
    path: str
    sha256: str
    mime: str
    size: int


def detect_image(data: bytes) -> tuple[str, str]:
    if not 100 <= len(data) <= 5_000_000:
        raise StorageError("A imagem precisa ter entre 100 bytes e 5 MB")
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg", ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in data[-40:]:
        return "image/png", ".png"
    raise StorageError("Formato inválido: envie JPEG ou PNG")


class SupabaseStorage:
    def __init__(self, url: str, service_key: str, *, opener=None):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL do Supabase deve ser HTTPS")
        if not service_key:
            raise ValueError("Chave de serviço ausente")
        self.url = url.rstrip("/")
        self.service_key = service_key
        self.opener = opener or urllib.request.urlopen

    @classmethod
    def from_environment(cls):
        from conrado.config import supabase_server_key

        return cls(os.environ.get("SUPABASE_URL", ""), supabase_server_key())

    def _request(self, path: str, payload: bytes, mime: str, *, method: str = "POST",
                 upsert: bool = False):
        headers = {
            "Authorization": "Bearer " + self.service_key,
            "apikey": self.service_key,
            "Content-Type": mime,
        }
        if upsert:
            headers["x-upsert"] = "false"
        req = urllib.request.Request(
            self.url + path,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with self.opener(req, timeout=20) as response:
                result = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # Nunca incluir a resposta de erro ou as credenciais em logs.
            raise StorageError("Falha ao comunicar com armazenamento privado") from exc
        return result

    @staticmethod
    def _validate_path(path: str) -> None:
        if not re.fullmatch(
            r"viagens/\d+/notas/\d+/[A-Za-z0-9-]{12,90}-[a-f0-9]{12}\.(jpg|png)", path
        ):
            raise StorageError("Caminho de evidência inválido")

    def upload(self, trip_id: int, note_id: int, event_id: str, content: bytes) -> UploadedEvidence:
        if trip_id <= 0 or note_id <= 0 or not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise StorageError("Identificação da evidência inválida")
        mime, ext = detect_image(content)
        sha = hashlib.sha256(content).hexdigest()
        path = f"viagens/{trip_id}/notas/{note_id}/{event_id}-{sha[:12]}{ext}"
        quoted = urllib.parse.quote(path, safe="/")
        result = self._request("/storage/v1/object/canhotos/" + quoted, content, mime, upsert=True)
        if not isinstance(result, dict):
            raise StorageError("Resposta inválida do armazenamento")
        return UploadedEvidence(path=path, sha256=sha, mime=mime, size=len(content))

    def signed_download_url(self, path: str, seconds: int = 300) -> str:
        self._validate_path(path)
        if not 1 <= seconds <= 300:
            raise StorageError("Prazo de acesso inválido")
        quoted = urllib.parse.quote(path, safe="/")
        result = self._request(
            "/storage/v1/object/sign/canhotos/" + quoted,
            json.dumps({"expiresIn": seconds}).encode(),
            "application/json",
        )
        if not isinstance(result, dict):
            raise StorageError("Resposta de URL temporária inválida")
        signed = result.get("signedURL") or result.get("signedUrl")
        if not isinstance(signed, str):
            raise StorageError("Resposta de URL temporária inválida")
        if signed.startswith("/object/sign/"):
            return self.url + "/storage/v1" + signed
        if signed.startswith("/storage/v1/object/sign/"):
            return self.url + signed
        raise StorageError("Resposta de URL temporária inválida")

    def delete(self, path: str) -> None:
        """Remove uma evidência pela API oficial, evitando objetos órfãos."""
        self._validate_path(path)
        result = self._request(
            "/storage/v1/object/canhotos",
            json.dumps({"prefixes": [path]}).encode(),
            "application/json",
            method="DELETE",
        )
        if not isinstance(result, (list, dict)):
            raise StorageError("Resposta de exclusão inválida do armazenamento")

    def exists(self, path: str) -> bool:
        """Consulta o índice autenticado do bucket para um caminho exato."""
        self._validate_path(path)
        folder, filename = path.rsplit("/", 1)
        result = self._request(
            "/storage/v1/object/list/canhotos",
            json.dumps({"prefix": folder, "limit": 2, "offset": 0, "search": filename}).encode(),
            "application/json",
        )
        if not isinstance(result, list):
            raise StorageError("Resposta de consulta inválida do armazenamento")
        return any(isinstance(item, dict) and item.get("name") == filename for item in result)

    def download(self, path: str) -> bytes:
        """Baixa uma evidência autenticada para backup, sem criar URL pública."""
        self._validate_path(path)
        quoted = urllib.parse.quote(path, safe="/")
        request = urllib.request.Request(
            self.url + "/storage/v1/object/authenticated/canhotos/" + quoted,
            headers={"Authorization": "Bearer " + self.service_key, "apikey": self.service_key},
            method="GET",
        )
        try:
            with self.opener(request, timeout=20) as response:
                content = response.read(5_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StorageError("Falha ao baixar evidência privada") from exc
        detect_image(content)
        return content

    def restore(self, path: str, content: bytes, expected_sha256: str) -> None:
        """Restaura um objeto validado em bucket vazio ou de recuperação."""
        self._validate_path(path)
        mime, extension = detect_image(content)
        if not path.endswith(extension) or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise StorageError("A evidência do backup falhou na verificação de integridade")
        quoted = urllib.parse.quote(path, safe="/")
        result = self._request(
            "/storage/v1/object/canhotos/" + quoted, content, mime, upsert=True,
        )
        if not isinstance(result, dict):
            raise StorageError("Resposta inválida ao restaurar evidência")
