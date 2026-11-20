"""Teste ponta a ponta do backend com NF-e integralmente fictícia.

O fluxo usa a API HTTP local conectada ao Supabase e remove, por IDs exatos,
todos os registros criados. Nenhum XML ou canhoto é persistido.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import struct
import sys
import threading
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

from conrado.config import load_env_file
from conrado.gestao import Server
from conrado.supabase_storage import SupabaseStorage


class E2EFailure(RuntimeError):
    """Falha identificada por etapa, sem transportar detalhes sensíveis."""


def synthetic_nfe(access_key: str, plate: str) -> bytes:
    if len(access_key) != 44 or not access_key.isdigit():
        raise ValueError("Chave fictícia inválida")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
  <NFe><infNFe Id="NFe{access_key}">
    <ide><nNF>999999</nNF></ide>
    <dest><CNPJ>00000000000000</CNPJ><xNome>CLIENTE FICTÍCIO E2E</xNome>
      <enderDest><xLgr>RUA TESTE</xLgr><nro>100</nro><xBairro>CENTRO</xBairro>
        <xMun>ITAÚNA</xMun><UF>MG</UF><CEP>35680000</CEP></enderDest>
    </dest>
    <det nItem="1"><prod><cProd>1940</cProd><xProd>WHEY FICTÍCIO</xProd>
      <qCom>1.0000</qCom><uCom>CX</uCom></prod></det>
    <transp><vol><qVol>1</qVol><pesoB>1.500</pesoB></vol></transp>
    <infAdic><infCpl>PLACA: {plate}; EMBARQUE: TESTE E2E</infCpl>
      <obsCont xCampo="Vendedor"><xTexto>IAN TESTE</xTexto></obsCont></infAdic>
  </infNFe></NFe>
</nfeProc>"""
    return xml.encode()


def synthetic_png() -> bytes:
    """Cria um PNG pequeno e válido sem incluir imagem ou documento real."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    pixels = b"".join(b"\x00" + b"\x2d\x7f\xb8" * 8 for _ in range(8))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\x00Canhoto sintetico exclusivo para teste E2E")
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def _request(base_url: str, path: str, *, token: str, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": "Bearer " + token}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url + path, data=payload, headers=headers,
                                     method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("erro", "falha HTTP")
        except (ValueError, AttributeError):
            detail = "falha HTTP"
        raise RuntimeError(f"Backend recusou o teste fictício ({exc.code}): {detail}") from exc


def _cleanup(dsn: str, ids: dict) -> None:
    import psycopg

    driver_id, vehicle_id = ids.get("driver_id"), ids.get("vehicle_id")
    trip_id, note_id = ids.get("trip_id"), ids.get("note_id")
    admin_id = ids.get("admin_id")
    with psycopg.connect(dsn, connect_timeout=10) as db:
        if note_id:
            db.execute("DELETE FROM app.evidence WHERE note_id=%s", (note_id,))
            db.execute("DELETE FROM app.delivery_events WHERE note_id=%s", (note_id,))
            db.execute("DELETE FROM app.delivery_starts WHERE note_id=%s", (note_id,))
        if trip_id:
            db.execute("DELETE FROM app.notes WHERE trip_id=%s", (trip_id,))
        if driver_id:
            db.execute("DELETE FROM app.driver_sessions WHERE driver_id=%s", (driver_id,))
            db.execute("DELETE FROM app.driver_auth WHERE driver_id=%s", (driver_id,))
        if admin_id:
            db.execute("DELETE FROM app.admin_sessions WHERE admin_id=%s", (admin_id,))
        audit_conditions, audit_params = [], []
        for kind, object_id in (("driver", driver_id), ("trip", trip_id), ("note", note_id),
                                ("admin_user", admin_id)):
            if object_id:
                audit_conditions.append("(object_type=%s AND object_id=%s)")
                audit_params.extend((kind, object_id))
        if audit_conditions:
            db.execute("DELETE FROM app.audit WHERE " + " OR ".join(audit_conditions), audit_params)
        if trip_id:
            db.execute("DELETE FROM app.trips WHERE id=%s", (trip_id,))
        if vehicle_id:
            db.execute("DELETE FROM app.vehicles WHERE id=%s", (vehicle_id,))
        if driver_id:
            db.execute("DELETE FROM app.drivers WHERE id=%s", (driver_id,))
        if admin_id:
            db.execute("DELETE FROM app.admin_users WHERE id=%s", (admin_id,))

    with psycopg.connect(dsn, connect_timeout=10) as db:
        checks = []
        for table, object_id in (("drivers", driver_id), ("vehicles", vehicle_id),
                                 ("trips", trip_id), ("notes", note_id),
                                 ("admin_users", admin_id)):
            if object_id:
                checks.append(bool(db.execute(
                    f"SELECT EXISTS (SELECT 1 FROM app.{table} WHERE id=%s)", (object_id,)
                ).fetchone()[0]))
    if any(checks):
        raise RuntimeError("A limpeza do teste ponta a ponta não foi confirmada")


def run_e2e(dsn: str, admin_token: str) -> dict:
    import psycopg

    if not dsn or len(admin_token) < 32:
        raise RuntimeError("Configuração local incompleta")
    marker = secrets.token_hex(6)
    plate = f"TST0A{secrets.randbelow(100):02d}"
    access_key = "98" + "".join(str(secrets.randbelow(10)) for _ in range(42))
    driver_password = secrets.token_urlsafe(24)
    admin_password = secrets.token_urlsafe(28)
    admin_username = "e2e" + marker
    ids: dict[str, int] = {}
    storage = SupabaseStorage.from_environment()
    storage_path = ""
    signed_url = ""
    rate_client = "e2e-client-" + marker
    server = Server(("127.0.0.1", 0), admin_token, Path("e2e-unused.sqlite"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    stage = "healthcheck do backend"
    try:
        if _request(base_url, "/healthz", token="").get("status") != "ok":
            raise RuntimeError("O healthcheck não respondeu corretamente")
        stage = "readiness do banco"
        if _request(base_url, "/readyz", token="").get("status") != "ready":
            raise RuntimeError("O backend não ficou pronto para receber tráfego")
        stage = "rate limiting persistente"
        for _ in range(5):
            server.record_login_failure(rate_client, plate)
        if not server.login_blocked(rate_client, plate):
            raise RuntimeError("O limitador persistente não bloqueou as tentativas")
        server.clear_login_failures(rate_client, plate)
        if server.login_blocked(rate_client, plate):
            raise RuntimeError("O limitador persistente não foi liberado com segurança")
        stage = "conta administrativa fictícia"
        admin = server.db.create_admin(admin_username, admin_password)
        ids["admin_id"] = admin["admin_id"]
        session = _request(base_url, "/api/admin/login", token="", body={
            "username": admin_username, "password": admin_password,
        })
        if session.get("user", {}).get("role") != "admin":
            raise RuntimeError("A conta fictícia não recebeu o perfil administrativo")
        admin_session = session["token"]
        identity = _request(base_url, "/api/admin/me", token=admin_session)
        if identity.get("admin_id") != ids["admin_id"] or identity.get("role") != "admin":
            raise RuntimeError("A sessão administrativa não preservou identidade e perfil")
        stage = "cadastro fictício"
        driver = _request(base_url, "/api/drivers", token=admin_session, body={
            "name": "MOTORISTA FICTÍCIO " + marker,
            "plate": plate,
            "vehicle_type": "TESTE",
        })
        ids.update(driver_id=driver["driver_id"], vehicle_id=driver["vehicle_id"])
        stage = "senha do motorista"
        _request(base_url, f"/api/drivers/{ids['driver_id']}/password", token=admin_session,
                 body={"password": driver_password})
        stage = "criação da viagem"
        trip = _request(base_url, "/api/trips", token=admin_session,
                        body={"vehicle_id": ids["vehicle_id"], "label": "E2E FICTÍCIO " + marker})
        ids["trip_id"] = trip["trip_id"]
        stage = "importação da NF-e"
        raw_xml = synthetic_nfe(access_key, plate)
        imported = _request(base_url, f"/api/trips/{ids['trip_id']}/import", token=admin_session, body={
            "arquivos": [{"name": "nfe-ficticia-e2e.xml", "base64": base64.b64encode(raw_xml).decode()}]
        })
        if imported.get("imported") != 1:
            raise RuntimeError("A NF-e fictícia não foi importada")
        stage = "liberação da viagem"
        _request(base_url, f"/api/trips/{ids['trip_id']}/release", token=admin_session, body={})
        stage = "atribuição da auditoria administrativa"
        with psycopg.connect(dsn, connect_timeout=10) as db:
            attributed, generic = db.execute("""SELECT
                    count(1) FILTER (WHERE actor=%s) AS attributed,
                    count(1) FILTER (WHERE actor=ANY(%s)) AS generic
                FROM app.audit
                WHERE (object_type=%s AND object_id=%s)
                   OR (object_type=%s AND object_id=%s)""",
                (f"admin:{ids['admin_id']}", ["admin", "admin:legacy"],
                 "driver", ids["driver_id"], "trip", ids["trip_id"]),
            ).fetchone()
        stage = (
            "atribuição da auditoria administrativa "
            f"(individual={attributed}, genérica={generic})"
        )
        if attributed < 5 or generic:
            raise RuntimeError("Os eventos administrativos não foram atribuídos à sessão individual")
        stage = "login do motorista"
        session = _request(base_url, "/api/driver/login", token="", body={
            "plate": plate, "password": driver_password,
        })
        driver_token = session["token"]
        stage = "consulta da viagem ativa"
        active = _request(base_url, "/api/driver/trip", token=driver_token)
        ids["note_id"] = active["trip"]["notes"][0]["id"]
        start_id = "e2e-" + marker + "-start"
        stage = "início do descarregamento com localização fictícia"
        started = _request(
            base_url, f"/api/driver/notes/{ids['note_id']}/start", token=driver_token,
            body={"start_id": start_id, "started_at": datetime.now(timezone.utc).isoformat(),
                  "latitude": -20.145, "longitude": -44.883, "accuracy_m": 15},
        )
        if started.get("start_id") != start_id or not started.get("location_recorded"):
            raise RuntimeError("O início do descarregamento não preservou horário e localização")
        event_id = "e2e-" + marker + "-event"
        photo = synthetic_png()
        photo_sha = hashlib.sha256(photo).hexdigest()
        storage_path = (
            f"viagens/{ids['trip_id']}/notas/{ids['note_id']}/"
            f"{event_id}-{photo_sha[:12]}.png"
        )
        stage = "baixa entregue com canhoto"
        delivery = _request(
            base_url, f"/api/driver/notes/{ids['note_id']}/status", token=driver_token,
            body={"event_id": event_id, "status": "delivered", "start_id": start_id,
                  "completed_at": datetime.now(timezone.utc).isoformat(),
                  "photo_base64": base64.b64encode(photo).decode()},
        )
        if delivery.get("status") != "delivered" or delivery.get("duration_seconds") is None:
            raise RuntimeError("A baixa entregue não preservou o tempo de atendimento")
        stage = "ocultação da entrega concluída"
        active = _request(base_url, "/api/driver/trip", token=driver_token)
        if active.get("trip", {}).get("notes"):
            raise RuntimeError("A entrega concluída ainda aparece na rota ativa")
        stage = "histórico administrativo da entrega"
        detail = _request(base_url, f"/api/trips/{ids['trip_id']}/detail", token=admin_session)
        event = detail["notes"][0]["events"][0]
        if event.get("status") != "delivered" or event.get("latitude") != -20.145:
            raise RuntimeError("O histórico não preservou a baixa e a localização")
        stage = "acesso administrativo ao canhoto"
        evidence_access = _request(
            base_url, f"/api/evidence/{event_id}/access", token=admin_session
        )
        signed_url = evidence_access.get("url", "")
        if evidence_access.get("expires_in") != 300 or not signed_url.startswith("https://"):
            raise RuntimeError("O acesso temporário ao canhoto ficou inválido")
        stage = "download privado do canhoto"
        with urllib.request.urlopen(signed_url, timeout=15) as response:
            if response.read() != photo:
                raise RuntimeError("O canhoto fictício não pôde ser validado")
        stage = "encerramento da viagem"
        closed = _request(base_url, f"/api/driver/trips/{ids['trip_id']}/close",
                          token=driver_token, body={})
        if closed.get("status") != "closed":
            raise RuntimeError("A viagem fictícia não foi encerrada")
        stage = "logout do motorista"
        _request(base_url, "/api/driver/logout", token=driver_token, body={})
        stage = "logout administrativo"
        _request(base_url, "/api/admin/logout", token=admin_session, body={})
        return {"imported": 1, "delivery": True, "timing": True,
                "route_hidden": True, "storage": True, "closed": True}
    except Exception as exc:
        raise E2EFailure(f"falha na etapa: {stage}") from exc
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        cleanup_issue = ""
        try:
            server.clear_login_failures(rate_client, plate)
        except Exception:
            cleanup_issue = "limpeza do limitador de login"
        if storage_path:
            try:
                storage.delete(storage_path)
                if storage.exists(storage_path):
                    cleanup_issue = "arquivo ainda listado após exclusão"
            except Exception:
                cleanup_issue = "exclusão pela API do Storage"
        try:
            _cleanup(dsn, ids)
        except Exception:
            cleanup_issue = "limpeza dos registros do banco"
        if cleanup_issue:
            raise E2EFailure("falha na limpeza: " + cleanup_issue)


def main() -> int:
    try:
        load_env_file()
        result = run_e2e(os.environ.get("DATABASE_URL", ""),
                         os.environ.get("CONRADO_ADMIN_TOKEN", ""))
    except Exception as exc:
        detail = str(exc) if isinstance(exc, E2EFailure) else "falha de configuração ou conexão"
        print(f"E2E pendente: {detail}. A rotina de limpeza foi executada.")
        return 1
    print("E2E aprovado: NF-e fictícia, início, baixa, canhoto privado, ocultação e encerramento validados.")
    print("Limpeza confirmada: nenhum registro ou arquivo fictício permaneceu.")
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
