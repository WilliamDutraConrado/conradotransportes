"""Smoke test transacional do Supabase, sempre revertido ao final.

Exercita as treze tabelas do schema privado com dados sintéticos. Não envia
arquivos ao Storage e confirma em uma segunda conexão que o rollback ocorreu.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

from conrado.config import ConfigError, load_env_file


def run_smoke_test(dsn: str, *, connect=None) -> dict:
    if not dsn:
        raise RuntimeError("DATABASE_URL não configurada")
    if connect is None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Dependência psycopg não instalada") from exc
        connect = psycopg.connect

    marker = "smoke-" + secrets.token_hex(8)
    plate = f"TST0A{secrets.randbelow(100):02d}"
    access_key = "99" + "".join(str(secrets.randbelow(10)) for _ in range(42))
    event_id = marker + "-event"
    token_hash = hashlib.sha256((marker + "-session").encode()).hexdigest()
    admin_token_hash = hashlib.sha256((marker + "-admin-session").encode()).hexdigest()

    with connect(dsn, connect_timeout=10) as db:
        try:
            driver_id = db.execute(
                "INSERT INTO app.drivers(name) VALUES (%s) RETURNING id", (marker,)
            ).fetchone()[0]
            vehicle_id = db.execute(
                "INSERT INTO app.vehicles(plate,vehicle_type,driver_id) VALUES (%s,%s,%s) RETURNING id",
                (plate, "TESTE", driver_id),
            ).fetchone()[0]
            trip_id = db.execute(
                "INSERT INTO app.trips(vehicle_id,driver_id,label) VALUES (%s,%s,%s) RETURNING id",
                (vehicle_id, driver_id, marker),
            ).fetchone()[0]
            note_id = db.execute(
                """INSERT INTO app.notes(trip_id,access_key,number,recipient_id,recipient,address,
                    city,seller,items,gross_weight,volumes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) RETURNING id""",
                (trip_id, access_key, "999999", "00000000000000", "CLIENTE FICTÍCIO",
                 "ENDEREÇO FICTÍCIO", "CIDADE TESTE", "VENDEDOR TESTE",
                 json.dumps([{"codigo": "TESTE", "descricao": "PRODUTO FICTÍCIO"}]), 1, 1),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO app.driver_auth(driver_id,salt,password_hash) VALUES (%s,%s,%s)",
                (driver_id, "0" * 32, "0" * 64),
            )
            db.execute(
                "INSERT INTO app.driver_sessions(token_hash,driver_id,expires_at) VALUES (%s,%s,%s)",
                (token_hash, driver_id, datetime.now(timezone.utc) + timedelta(minutes=5)),
            )
            admin_id = db.execute(
                """INSERT INTO app.admin_users(username,salt,password_hash,role)
                    VALUES (%s,%s,%s,%s) RETURNING id""",
                (("adm" + secrets.token_hex(6))[:15], "0" * 32, "0" * 64, "admin"),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO app.admin_sessions(token_hash,admin_id,expires_at) VALUES (%s,%s,%s)",
                (admin_token_hash, admin_id, datetime.now(timezone.utc) + timedelta(minutes=5)),
            )
            db.execute(
                "INSERT INTO app.login_limits(key_hash,failures) VALUES (%s,%s)",
                (hashlib.sha256((marker + "-limit").encode()).hexdigest(), 1),
            )
            db.execute(
                """INSERT INTO app.delivery_starts(id,note_id,driver_id,started_at,recorded_at,
                    latitude,longitude,accuracy_m) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (marker + "-start", note_id, driver_id, datetime.now(timezone.utc),
                 datetime.now(timezone.utc), -20.145, -44.883, 15),
            )
            db.execute(
                """INSERT INTO app.delivery_events(id,note_id,driver_id,status,reason,arrival_time,
                    affected_item,seller_name,start_id,completed_at,occurrence_type,observation)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (event_id, note_id, driver_id, "occurrence", "TESTE COM ROLLBACK", "11:00",
                 "ITEM FICTÍCIO", "VENDEDOR TESTE", marker + "-start",
                 datetime.now(timezone.utc), "general", "OBSERVAÇÃO FICTÍCIA"),
            )
            db.execute(
                """INSERT INTO app.evidence(note_id,event_id,storage_path,mime,sha256)
                    VALUES (%s,%s,%s,%s,%s)""",
                (note_id, event_id, f"smoke/{marker}.jpg", "image/jpeg", "0" * 64),
            )
            db.execute(
                """INSERT INTO app.audit(actor,event,object_type,object_id,payload)
                    VALUES (%s,%s,%s,%s,%s::jsonb)""",
                ("smoke", "tested", "trip", trip_id, json.dumps({"marker": marker})),
            )
        finally:
            db.rollback()

    with connect(dsn, connect_timeout=10) as db:
        persisted = bool(db.execute(
            "SELECT EXISTS (SELECT 1 FROM app.drivers WHERE name=%s)", (marker,)
        ).fetchone()[0])
    if persisted:
        raise RuntimeError("O rollback do teste transacional não foi confirmado")
    return {"tables_exercised": 13, "rollback_confirmed": True}


def main() -> int:
    try:
        load_env_file()
        result = run_smoke_test(os.environ.get("DATABASE_URL", ""))
    except (ConfigError, RuntimeError, OSError, ValueError):
        print("Smoke test pendente: falha segura na conexão ou transação; confira a configuração local.")
        return 1
    print(f"Smoke test aprovado: {result['tables_exercised']} tabelas exercitadas; rollback confirmado.")
    print("Nenhum dado fictício permaneceu no banco e nenhum arquivo foi enviado ao Storage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
