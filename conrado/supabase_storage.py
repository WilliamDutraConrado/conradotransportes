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
        return cls(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    def _request(self, path: str, payload: bytes, mime: str) -> dict:
        req = urllib.request.Request(
            self.url + path,
            data=payload,
            headers={
                "Authorization": "Bearer " + self.service_key,
                "apikey": self.service_key,
                "Content-Type": mime,
                "x-upsert": "false",
            },
            method="POST",
        )
        try:
            with self.opener(req, timeout=20) as response:
                result = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            # Nunca incluir a resposta de erro ou as credenciais em logs.
            raise StorageError("Falha ao comunicar com armazenamento privado") from exc
        if not isinstance(result, dict):
            raise StorageError("Resposta inválida do armazenamento")
        return result

    def upload(self, trip_id: int, note_id: int, event_id: str, content: bytes) -> UploadedEvidence:
        if trip_id <= 0 or note_id <= 0 or not re.fullmatch(r"[A-Za-z0-9-]{12,90}", event_id):
            raise StorageError("Identificação da evidência inválida")
        mime, ext = detect_image(content)
        sha = hashlib.sha256(content).hexdigest()
        path = f"viagens/{trip_id}/notas/{note_id}/{event_id}-{sha[:12]}{ext}"
        quoted = urllib.parse.quote(path, safe="/")
        self._request("/storage/v1/object/canhotos/" + quoted, content, mime)
        return UploadedEvidence(path=path, sha256=sha, mime=mime, size=len(content))

    def signed_download_url(self, path: str, seconds: int = 300) -> str:
        if not 1 <= seconds <= 300 or not re.fullmatch(r"viagens/\d+/notas/\d+/[A-Za-z0-9-]{12,90}-[a-f0-9]{12}\.(jpg|png)", path):
            raise StorageError("Caminho ou prazo de acesso inválido")
        quoted = urllib.parse.quote(path, safe="/")
        result = self._request(
            "/storage/v1/object/sign/canhotos/" + quoted,
            json.dumps({"expiresIn": seconds}).encode(),
            "application/json",
        )
        signed = result.get("signedURL") or result.get("signedUrl")
        if not isinstance(signed, str) or not signed.startswith("/storage/v1/object/sign/"):
            raise StorageError("Resposta de URL temporária inválida")
        return self.url + signed
