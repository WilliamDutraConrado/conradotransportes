"""Torre de controle: consolida posição e desempenho diário de cada motorista.

As duas persistências (SQLite e PostgreSQL) entregam os mesmos dados brutos por
``tower_raw``; este módulo transforma esses dados no painel operacional, sem
consultar o banco. Assim a regra de negócio fica em um único lugar e testável.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Sem posição GPS mais recente que isso, o motorista em rota é sinalizado.
SIGNAL_STALE_SECONDS = 10 * 60
# Atendimento (descarregamento) aberto há mais tempo que isso gera alerta.
LONG_SERVICE_SECONDS = 40 * 60
# Posições recebidas há menos tempo que isso são descartadas para poupar banco.
POSITION_MIN_INTERVAL_SECONDS = 15
# Retenção do histórico de posições (minimização de dados pessoais).
POSITION_RETENTION_DAYS = 30
TIMELINE_LIMIT = 30
TRAIL_LIMIT = 400
ISSUE_STATUSES = ("partial", "refused", "occurrence")


def local_zone():
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        # Mesmo fallback de store.occurrence_message: São Paulo opera em UTC-3 desde 2019.
        return timezone(timedelta(hours=-3), "America/Sao_Paulo")


def day_start(moment: datetime | None = None) -> datetime:
    """Meia-noite de hoje no horário de São Paulo, convertida para UTC."""
    moment = (moment or datetime.now(timezone.utc)).astimezone(local_zone())
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def parse_time(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def iso(value) -> str | None:
    moment = parse_time(value)
    return moment.isoformat() if moment else None


def seconds_between(start, end) -> int | None:
    started, finished = parse_time(start), parse_time(end)
    if not started or not finished:
        return None
    return max(0, int((finished - started).total_seconds()))


def _number(value):
    return None if value is None else float(value)


def _effective_events(events: list[dict]) -> list[dict]:
    """Última baixa de cada NF-e no dia, ignorando baixas que foram estornadas.

    Uma nota estornada volta para ``pending``; se for baixada de novo, o evento
    mais recente volta a coincidir com a situação atual da nota.
    """
    latest: dict[int, dict] = {}
    for event in sorted(events, key=lambda item: (parse_time(item["created_at"]) or datetime.min.replace(tzinfo=timezone.utc), str(item["id"]))):
        latest[event["note_id"]] = event
    return [event for event in latest.values() if event.get("note_status") == event["status"]]


def downsample(points: list[dict], limit: int = TRAIL_LIMIT) -> list[dict]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(index * step)] for index in range(limit)]


def build_tower(raw: dict, moment: datetime | None = None) -> dict:
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = day_start(moment)

    trips_by_driver: dict[int, list[dict]] = {}
    for trip in raw.get("trips", []):
        trips_by_driver.setdefault(trip["driver_id"], []).append(trip)
    events_by_driver: dict[int, list[dict]] = {}
    for event in raw.get("events", []):
        events_by_driver.setdefault(event["driver_id"], []).append(event)
    starts_by_driver: dict[int, list[dict]] = {}
    for start in raw.get("active_starts", []):
        starts_by_driver.setdefault(start["driver_id"], []).append(start)
    positions = {position["driver_id"]: position for position in raw.get("positions", [])}

    drivers, alerts, delivery_points, timeline = [], [], [], []
    service_durations: list[int] = []
    for driver in raw.get("drivers", []):
        driver_id = driver["driver_id"]
        driver_trips = trips_by_driver.get(driver_id, [])
        active_trip = next((trip for trip in driver_trips if trip["status"] == "released"), None)
        closed_today = [trip for trip in driver_trips if trip["status"] == "closed"
                        and (parse_time(trip.get("closed_at")) or since) >= since]

        all_events = events_by_driver.get(driver_id, [])
        effective = _effective_events(all_events)
        delivered = [event for event in effective if event["status"] == "delivered"]
        issues = [event for event in effective if event["status"] in ISSUE_STATUSES]
        durations = [value for value in (seconds_between(event.get("started_at"), event.get("completed_at") or event.get("created_at"))
                                         for event in effective) if value is not None]
        service_durations.extend(durations)
        done = len(delivered) + len(issues)
        times = sorted(filter(None, (parse_time(event.get("completed_at") or event["created_at"]) for event in effective)))

        starts = sorted(starts_by_driver.get(driver_id, []),
                        key=lambda item: parse_time(item["started_at"]) or moment, reverse=True)
        active_service = None
        if starts:
            start = starts[0]
            active_service = {
                "note_id": start["note_id"], "number": start.get("number"),
                "recipient": start.get("recipient"), "city": start.get("city"),
                "started_at": iso(start["started_at"]),
                "elapsed_seconds": seconds_between(start["started_at"], moment),
            }

        position = None
        gps = positions.get(driver_id)
        if gps:
            position = {
                "latitude": _number(gps["latitude"]), "longitude": _number(gps["longitude"]),
                "accuracy_m": _number(gps.get("accuracy_m")), "speed_kmh": _number(gps.get("speed_kmh")),
                "heading": _number(gps.get("heading")), "recorded_at": iso(gps["recorded_at"]),
                "source": "gps",
            }
        else:
            # Sem GPS contínuo, usa a última localização pontual registrada no dia.
            located = [item for item in [*starts, *all_events] if item.get("latitude") is not None]
            located.sort(key=lambda item: parse_time(item.get("started_at") or item.get("created_at")) or since)
            if located:
                last = located[-1]
                position = {
                    "latitude": _number(last["latitude"]), "longitude": _number(last["longitude"]),
                    "accuracy_m": _number(last.get("accuracy_m")), "speed_kmh": None, "heading": None,
                    "recorded_at": iso(last.get("started_at") or last.get("created_at")),
                    "source": "delivery",
                }
        signal_age = seconds_between(position["recorded_at"], moment) if position else None

        if active_trip and active_service:
            status = "in_service"
        elif active_trip and (signal_age is None or signal_age > SIGNAL_STALE_SECONDS):
            status = "no_signal"
        elif active_trip:
            status = "in_route"
        elif done or closed_today:
            status = "finished"
        else:
            status = "available"

        trip_payload = None
        if active_trip:
            total = int(active_trip.get("notes") or 0)
            pending = int(active_trip.get("pending") or 0)
            trip_payload = {
                "id": active_trip["id"], "label": active_trip["label"],
                "released_at": iso(active_trip.get("released_at")),
                "notes": total, "pending": pending,
                "delivered": int(active_trip.get("delivered") or 0),
                "issues": int(active_trip.get("issues") or 0),
                "progress": round(100 * (total - pending) / total, 1) if total else 0.0,
            }

        drivers.append({
            "driver_id": driver_id, "name": driver["name"], "plate": driver["plate"],
            "vehicle_type": driver.get("vehicle_type", ""), "status": status,
            "trip": trip_payload, "position": position, "signal_age_seconds": signal_age,
            "active_service": active_service,
            "today": {
                "delivered": len(delivered), "issues": len(issues), "done": done,
                "success_rate": round(100 * len(delivered) / done, 1) if done else None,
                "avg_service_seconds": round(sum(durations) / len(durations)) if durations else None,
                "first_at": times[0].isoformat() if times else None,
                "last_at": times[-1].isoformat() if times else None,
                "trips_closed": len(closed_today),
            },
        })

        label = f"{driver['name']} • {driver['plate']}"
        if status == "no_signal":
            alerts.append({
                "severity": "warning", "kind": "no_signal", "driver_id": driver_id,
                "title": "Sem sinal de localização",
                "detail": label + (f" • último sinal há {signal_age // 60} min" if signal_age is not None else " • nenhum sinal hoje"),
                "at": position["recorded_at"] if position else None,
            })
        if active_service and (active_service["elapsed_seconds"] or 0) > LONG_SERVICE_SECONDS:
            alerts.append({
                "severity": "warning", "kind": "long_service", "driver_id": driver_id,
                "title": "Atendimento prolongado",
                "detail": f"{label} • NF-e {active_service['number']} • {active_service['elapsed_seconds'] // 60} min",
                "at": active_service["started_at"],
            })
        for event in issues:
            alerts.append({
                "severity": "critical", "kind": event["status"], "driver_id": driver_id,
                "title": {"refused": "Recusa", "partial": "Falta parcial"}.get(event["status"], "Ocorrência"),
                "detail": f"{label} • NF-e {event.get('number')} • {event.get('recipient') or ''}".rstrip(" •"),
                "at": iso(event.get("completed_at") or event["created_at"]),
            })
        for event in effective:
            if event.get("latitude") is not None:
                delivery_points.append({
                    "driver_id": driver_id, "status": event["status"], "number": event.get("number"),
                    "recipient": event.get("recipient"), "latitude": _number(event["latitude"]),
                    "longitude": _number(event["longitude"]), "at": iso(event.get("completed_at") or event["created_at"]),
                })
        for event in all_events:
            timeline.append({
                "driver_id": driver_id, "driver": driver["name"], "plate": driver["plate"],
                "status": event["status"], "number": event.get("number"), "recipient": event.get("recipient"),
                "city": event.get("city"), "at": iso(event.get("completed_at") or event["created_at"]),
                "reverted": event.get("note_status") != event["status"],
            })

    order = {"critical": 0, "warning": 1}
    alerts.sort(key=lambda item: item["at"] or "", reverse=True)
    alerts.sort(key=lambda item: order.get(item["severity"], 2))
    timeline.sort(key=lambda item: item["at"] or "", reverse=True)
    delivered_total = sum(item["today"]["delivered"] for item in drivers)
    issues_total = sum(item["today"]["issues"] for item in drivers)
    done_total = delivered_total + issues_total
    en_route = [item for item in drivers if item["trip"]]
    return {
        "generated_at": moment.isoformat(),
        "day_start": since.isoformat(),
        "signal_stale_seconds": SIGNAL_STALE_SECONDS,
        "totals": {
            "drivers": len(drivers),
            "in_route": len(en_route),
            "with_signal": sum(
                1 for item in en_route
                if item["position"]
                and item["position"]["source"] == "gps"
                and item["signal_age_seconds"] is not None
                and item["signal_age_seconds"] <= SIGNAL_STALE_SECONDS
            ),
            "in_service": sum(1 for item in drivers if item["status"] == "in_service"),
            "no_signal": sum(1 for item in drivers if item["status"] == "no_signal"),
            "delivered_today": delivered_total,
            "issues_today": issues_total,
            "pending_in_route": sum(item["trip"]["pending"] for item in en_route),
            "notes_in_route": sum(item["trip"]["notes"] for item in en_route),
            "success_rate": round(100 * delivered_total / done_total, 1) if done_total else None,
            "avg_service_seconds": round(sum(service_durations) / len(service_durations)) if service_durations else None,
        },
        "drivers": drivers,
        "alerts": alerts,
        "delivery_points": delivery_points,
        "timeline": timeline[:TIMELINE_LIMIT],
    }
