import base64
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from conrado.gestao import Server
from conrado.tower import (
    LONG_SERVICE_SECONDS, SIGNAL_STALE_SECONDS, build_tower, day_start, downsample,
)
from test_importer import xml

NOW = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)  # 12:00 em São Paulo


def at(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def driver(driver_id, name="Ana Souza", plate="ABC1D23"):
    return {"driver_id": driver_id, "name": name, "plate": plate, "vehicle_type": "3/4"}


def trip(driver_id, trip_id=None, *, status="released", notes=4, pending=2, delivered=1, issues=1, closed_at=None):
    return {"id": trip_id or driver_id * 10, "driver_id": driver_id, "label": f"Rota {driver_id}",
            "status": status, "released_at": at(240), "closed_at": closed_at,
            "notes": notes, "pending": pending, "delivered": delivered, "issues": issues}


def event(driver_id, note_id, status, minutes_ago, *, note_status=None, event_id=None, lat=None):
    return {"id": event_id or f"ev-{driver_id}-{note_id}-{minutes_ago}", "driver_id": driver_id,
            "note_id": note_id, "status": status, "created_at": at(minutes_ago),
            "completed_at": at(minutes_ago), "started_at": at(minutes_ago + 12),
            "latitude": lat, "longitude": None if lat is None else -44.88, "accuracy_m": None if lat is None else 10,
            "note_status": note_status or status, "number": str(100 + note_id), "recipient": "Cliente",
            "city": "Divinópolis", "trip_id": driver_id * 10}


class BuildTowerTests(unittest.TestCase):
    def test_day_start_uses_sao_paulo_midnight(self):
        self.assertEqual(day_start(NOW), datetime(2026, 11, 21, 3, 0, tzinfo=timezone.utc))
        # 01:00 UTC ainda é o dia anterior em São Paulo.
        self.assertEqual(day_start(datetime(2026, 11, 22, 1, 0, tzinfo=timezone.utc)),
                         datetime(2026, 11, 21, 3, 0, tzinfo=timezone.utc))

    def test_classifies_drivers_and_counts_only_effective_events(self):
        raw = {
            "drivers": [driver(1), driver(2, "Bruno Lima", "BRA2E19"), driver(3, "Carla Dias", "CAR3F20"),
                        driver(4, "Davi Rocha", "DAV4G21"), driver(5, "Eva Melo", "EVA5H22")],
            "trips": [trip(1), trip(2), trip(3), trip(4, status="closed", closed_at=at(30), pending=0)],
            "events": [
                event(1, 1, "delivered", 90, lat=-20.1),
                event(1, 2, "refused", 60),
                # Baixa estornada: a nota voltou a ficar pendente e não conta.
                event(1, 3, "delivered", 50, note_status="pending"),
                event(4, 7, "delivered", 40),
            ],
            "active_starts": [{"driver_id": 2, "note_id": 5, "started_at": at(LONG_SERVICE_SECONDS // 60 + 5),
                               "latitude": -20.2, "longitude": -44.9, "accuracy_m": 15,
                               "number": "555", "recipient": "Mercado", "city": "Itaúna"}],
            "positions": [
                {"driver_id": 1, "latitude": -20.14, "longitude": -44.88, "accuracy_m": 12, "speed_kmh": 42.0,
                 "heading": 90.0, "recorded_at": at(1)},
                {"driver_id": 3, "latitude": -20.3, "longitude": -44.7, "accuracy_m": 12, "speed_kmh": None,
                 "heading": None, "recorded_at": at(SIGNAL_STALE_SECONDS // 60 + 5)},
            ],
        }
        result = build_tower(raw, NOW)
        by_id = {item["driver_id"]: item for item in result["drivers"]}
        self.assertEqual(by_id[1]["status"], "in_route")
        self.assertEqual(by_id[2]["status"], "in_service")
        self.assertEqual(by_id[3]["status"], "no_signal")
        self.assertEqual(by_id[4]["status"], "finished")
        self.assertEqual(by_id[5]["status"], "available")

        self.assertEqual(by_id[1]["today"]["delivered"], 1)
        self.assertEqual(by_id[1]["today"]["issues"], 1)
        self.assertEqual(by_id[1]["today"]["success_rate"], 50.0)
        self.assertEqual(by_id[1]["today"]["avg_service_seconds"], 12 * 60)
        self.assertEqual(by_id[1]["position"]["source"], "gps")
        self.assertEqual(by_id[1]["trip"]["progress"], 50.0)
        # Sem GPS contínuo, usa a localização pontual do início de descarregamento.
        self.assertEqual(by_id[2]["position"]["source"], "delivery")
        self.assertEqual(by_id[2]["active_service"]["number"], "555")

        totals = result["totals"]
        self.assertEqual(totals["delivered_today"], 2)
        self.assertEqual(totals["issues_today"], 1)
        self.assertEqual(totals["in_route"], 3)
        self.assertEqual(totals["with_signal"], 2)
        self.assertEqual(totals["no_signal"], 1)

        kinds = [alert["kind"] for alert in result["alerts"]]
        self.assertEqual(kinds[0], "refused")  # críticos primeiro
        self.assertIn("no_signal", kinds)
        self.assertIn("long_service", kinds)
        self.assertEqual(len(result["delivery_points"]), 1)
        reverted = [item for item in result["timeline"] if item["reverted"]]
        self.assertEqual(len(reverted), 1)

    def test_downsample_keeps_endpoints(self):
        points = [{"i": index} for index in range(1000)]
        sample = downsample(points, 50)
        self.assertEqual(len(sample), 50)
        self.assertEqual((sample[0]["i"], sample[-1]["i"]), (0, 999))


class TowerAPITest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.admin_token = "segredo-para-teste-1234567890-abcd"
        self.server = Server(("127.0.0.1", 0), self.admin_token, Path(self.folder.name) / "test.db")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.folder.cleanup()

    def request(self, path, body=None, *, token=None, admin=True, headers_out=False):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif admin:
            headers["Authorization"] = f"Bearer {self.admin_token}"
        request = Request(f"http://127.0.0.1:{self.server.server_port}{path}",
                          data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                result = (response.status, json.load(response), dict(response.headers))
        except HTTPError as exc:
            try:
                result = (exc.code, json.load(exc), dict(exc.headers))
            finally:
                exc.close()
        return result if headers_out else result[:2]

    def prepare_route(self, *, release=True):
        _, created = self.request("/api/drivers", {"name": "Ana Souza", "plate": "ABC1D23", "vehicle_type": "3/4"})
        self.request(f"/api/drivers/{created['driver_id']}/password", {"password": "48271"})
        _, trip = self.request("/api/trips", {"vehicle_id": created["vehicle_id"], "label": "Rota centro"})
        payload = {"arquivos": [
            {"name": f"nf{index}.xml", "base64": base64.b64encode(xml(str(index) * 44)).decode()}
            for index in (1, 2)
        ]}
        self.assertEqual(self.request(f"/api/trips/{trip['trip_id']}/import", payload)[0], 200)
        if release:
            self.assertEqual(self.request(f"/api/trips/{trip['trip_id']}/release", {})[0], 200)
        _, login = self.request("/api/driver/login", {"plate": "ABC1D23", "password": "48271"}, admin=False)
        return created, trip, login["token"]

    def position(self, token, **overrides):
        body = {"latitude": -20.145, "longitude": -44.883, "accuracy_m": 14, "speed": 10, "heading": 180,
                "recorded_at": datetime.now(timezone.utc).isoformat(), **overrides}
        return self.request("/api/driver/position", body, token=token, admin=False)

    def test_position_requires_driver_session_and_active_trip(self):
        self.assertEqual(self.request("/api/driver/position", {"latitude": 1}, admin=False)[0], 401)
        _, _, token = self.prepare_route(release=False)
        status, result = self.position(token)
        self.assertEqual((status, result), (200, {"recorded": False, "reason": "no_active_trip"}))

    def test_position_is_validated_and_throttled(self):
        _, _, token = self.prepare_route()
        status, result = self.position(token, latitude=120)
        self.assertEqual(status, 400)
        status, result = self.position(token, recorded_at="2020-01-01T00:00:00+00:00")
        self.assertEqual(status, 400)
        self.assertEqual(self.position(token), (200, {"recorded": True}))
        self.assertEqual(self.position(token), (200, {"recorded": False, "reason": "throttled"}))

    def test_tower_reports_live_position_daily_performance_and_trail(self):
        created, trip, token = self.prepare_route()
        self.assertEqual(self.request("/api/admin/tower", admin=False)[0], 401)
        self.assertEqual(self.position(token), (200, {"recorded": True}))

        _, route = self.request("/api/driver/trip", token=token, admin=False)
        note_id = route["trip"]["notes"][0]["id"]
        status, started = self.request(f"/api/driver/notes/{note_id}/start", {
            "start_id": "inicio-torre-123456", "started_at": datetime.now(timezone.utc).isoformat(),
        }, token=token, admin=False)
        self.assertEqual(status, 200)
        status, tower = self.request("/api/admin/tower")
        self.assertEqual(tower["drivers"][0]["status"], "in_service")
        self.assertEqual(tower["totals"]["in_service"], 1)

        jpeg = base64.b64encode(b"\xff\xd8\xff" + b"canhoto-ficticio" * 10 + b"\xff\xd9").decode()
        status, _ = self.request(f"/api/driver/notes/{note_id}/status", {
            "event_id": "evento-torre-123456", "status": "delivered", "photo_base64": jpeg,
            "start_id": started["start_id"], "completed_at": datetime.now(timezone.utc).isoformat(),
        }, token=token, admin=False)
        self.assertEqual(status, 200)

        status, tower = self.request("/api/admin/tower")
        self.assertEqual(status, 200)
        item = tower["drivers"][0]
        self.assertEqual(item["driver_id"], created["driver_id"])
        self.assertEqual(item["status"], "in_route")
        self.assertEqual(item["position"]["source"], "gps")
        self.assertAlmostEqual(item["position"]["speed_kmh"], 36.0)
        self.assertEqual(item["trip"]["id"], trip["trip_id"])
        self.assertEqual((item["trip"]["notes"], item["trip"]["pending"]), (2, 1))
        self.assertEqual(item["today"]["delivered"], 1)
        self.assertEqual(tower["totals"]["delivered_today"], 1)
        self.assertEqual(tower["totals"]["success_rate"], 100.0)
        self.assertEqual(tower["timeline"][0]["status"], "delivered")

        status, trail = self.request(f"/api/admin/tower/drivers/{created['driver_id']}/trail")
        self.assertEqual(status, 200)
        self.assertEqual(len(trail["points"]), 1)

    def test_tower_page_allows_only_map_origins(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        with urlopen(base + "/gestao", timeout=5) as response:
            csp = response.headers["Content-Security-Policy"]
            html = response.read().decode()
        self.assertIn("script-src 'self' https://unpkg.com", csp)
        self.assertIn("https://tile.openstreetmap.org", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertIn('integrity="sha256-', html)
        self.assertIn("towerMap", html)
        _, _, headers = self.request("/api/drivers", headers_out=True)
        self.assertNotIn("unpkg", headers["Content-Security-Policy"])
        with urlopen(base + "/assets/tower.css", timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "text/css")


if __name__ == "__main__":
    unittest.main()
