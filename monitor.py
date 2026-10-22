"""Local telemetry service. Python stdlib + optional PostgreSQL/MQTT adapters."""
import argparse
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOG = logging.getLogger("monitor")
ROOT = Path(__file__).parent
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
VERSION = re.compile(r"[A-Za-z0-9_.-]{1,40}\Z")
FIELDS = {"device_id", "boot_id", "seq", "uptime_ms", "metric", "value", "source", "firmware"}
SCHEMA = """CREATE TABLE IF NOT EXISTS readings (
 device_id TEXT NOT NULL, boot_id TEXT NOT NULL, seq BIGINT NOT NULL,
 uptime_ms BIGINT NOT NULL, metric TEXT NOT NULL, value DOUBLE PRECISION NOT NULL,
 source TEXT NOT NULL, firmware TEXT NOT NULL, received_at TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(device_id, boot_id, seq));"""


def validate(raw):
    if not isinstance(raw, bytes) or len(raw) > 1024:
        raise ValueError("payload must be at most 1024 bytes")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid JSON") from exc
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("unexpected or missing fields")
    for key in ("device_id", "boot_id", "firmware"):
        pattern = VERSION if key == "firmware" else IDENTIFIER
        if not isinstance(data[key], str) or not pattern.fullmatch(data[key]):
            raise ValueError("invalid " + key)
    for key in ("seq", "uptime_ms"):
        if type(data[key]) is not int or not 0 <= data[key] <= 2**63 - 1:
            raise ValueError("invalid " + key)
    if data["metric"] not in ("temperature_c", "adc_raw"):
        raise ValueError("unsupported metric")
    if data["source"] not in ("simulated", "hardware"):
        raise ValueError("unsupported source")
    v = data["value"]
    if type(v) not in (int, float) or not math.isfinite(v):
        raise ValueError("value must be finite numeric")
    low, high = (-100, 200) if data["metric"] == "temperature_c" else (0, 4095)
    if not low <= v <= high:
        raise ValueError("value outside metric range")
    return data


class Store:
    def __init__(self, database):
        self.database = database
        self.postgres = database.startswith(("postgres://", "postgresql://"))
        self.lock = threading.Lock()
        if not self.postgres:
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute(SCHEMA)
            conn.execute("CREATE INDEX IF NOT EXISTS readings_recent ON readings(received_at)")

    @contextmanager
    def connection(self):
        if self.postgres:
            import psycopg
            with psycopg.connect(self.database) as conn:
                yield conn  # commits before the caller acknowledges
        else:
            conn = sqlite3.connect(self.database, timeout=10)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                with conn:
                    yield conn
            finally:
                conn.close()

    def sql(self, statement):
        return statement.replace("?", "%s") if self.postgres else statement

    def ingest(self, raw, topic=None):
        data = validate(raw)
        if topic is not None and topic != f"devices/{data['device_id']}/telemetry":
            raise ValueError("topic does not match device_id")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        identity = (data["device_id"], data["boot_id"], data["seq"])
        with self.lock, self.connection() as conn:
            row = conn.execute(self.sql("INSERT INTO readings VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(device_id,boot_id,seq) DO NOTHING RETURNING seq"),
                (*identity, data["uptime_ms"], data["metric"], data["value"], data["source"],
                 data["firmware"], datetime.now(timezone.utc).isoformat(), canonical)).fetchone()
            inserted = row is not None
            if not inserted:
                old = conn.execute(self.sql("SELECT payload FROM readings WHERE device_id=? AND boot_id=? AND seq=?"), identity).fetchone()
                if old[0] != canonical:
                    raise ValueError("identity collision: payload changed")
        return {"device_id": identity[0], "boot_id": identity[1], "seq": identity[2],
                "status": "stored" if inserted else "duplicate"}

    def history(self, device=None, limit=100):
        where, params = (" WHERE device_id=?", [device]) if device else ("", [])
        with self.connection() as conn:
            rows = conn.execute(self.sql("SELECT payload,received_at FROM readings" + where +
                " ORDER BY received_at DESC LIMIT ?"), (*params, limit)).fetchall()
        return [dict(json.loads(payload), received_at=received) for payload, received in rows]

    def devices(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT payload,received_at FROM (SELECT payload,received_at,"
                "ROW_NUMBER() OVER(PARTITION BY device_id ORDER BY received_at DESC) AS n FROM readings) AS ranked WHERE n=1").fetchall()
        now = datetime.now(timezone.utc)
        result = []
        for payload, received in rows:
            age = max(0, (now - datetime.fromisoformat(received)).total_seconds())
            result.append(dict(json.loads(payload), received_at=received, age_seconds=round(age, 1),
                               status="recent" if age <= 15 else "stale"))
        return sorted(result, key=lambda x: x["device_id"])


def handler(store):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, content_type="application/json"):
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            route = urlparse(self.path)
            try:
                if route.path == "/":
                    self.send(200, (ROOT / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
                elif route.path == "/health":
                    store.history(limit=1)
                    self.send(200, {"status": "ok", "database": "postgresql" if store.postgres else "sqlite"})
                elif route.path == "/api/devices":
                    self.send(200, store.devices())
                elif route.path == "/api/readings":
                    query = parse_qs(route.query)
                    limit = int(query.get("limit", ["100"])[0])
                    if not 1 <= limit <= 500:
                        raise ValueError("limit must be 1..500")
                    device = query.get("device_id", [None])[0]
                    if device is not None and not IDENTIFIER.fullmatch(device):
                        raise ValueError("invalid device_id")
                    self.send(200, store.history(device, limit))
                else:
                    self.send(404, {"error": "not found"})
            except ValueError as exc:
                self.send(400, {"error": str(exc)})
            except Exception:
                LOG.exception("read failed")
                self.send(503, {"error": "storage unavailable"})

        def do_POST(self):
            if self.path != "/api/telemetry":
                self.send(404, {"error": "not found"})
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Content-Type must be application/json")
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("chunked bodies unsupported")
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 1024:
                    raise ValueError("body must be 1..1024 bytes")
                self.connection.settimeout(5)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError("incomplete body")
                self.send(200, store.ingest(raw))
            except (ValueError, TimeoutError) as exc:
                self.send(400, {"error": str(exc)})
            except Exception:
                LOG.exception("ingestion failed")
                self.send(503, {"error": "storage unavailable; retry same identity"})

        def log_message(self, fmt, *args):
            LOG.info(fmt, *args)
    return Handler


def mqtt_ingestion(store, host, port):
    import paho.mqtt.client as mqtt
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="monitor-ingestor")
    def connected(c, userdata, flags, reason, properties):
        if reason == 0:
            c.subscribe("devices/+/telemetry", qos=1)
    def message(c, userdata, msg):
        try:
            ack = store.ingest(msg.payload, msg.topic)
        except ValueError as exc:
            LOG.warning("rejected telemetry: %s", exc)
            return
        except Exception:
            LOG.exception("storage failed; no application ACK")
            return
        c.publish(f"devices/{ack['device_id']}/ack", json.dumps(ack), qos=1, retain=False)
    client.on_connect, client.on_message = connected, message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    return client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--database", default=os.getenv("DATABASE_URL", "data/monitor.sqlite"))
    parser.add_argument("--mqtt-host", default=os.getenv("MQTT_HOST"))
    parser.add_argument("--mqtt-port", type=int, default=1883)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(args.database)
    client = mqtt_ingestion(store, args.mqtt_host, args.mqtt_port) if args.mqtt_host else None
    server = ThreadingHTTPServer((args.host, args.port), handler(store))
    LOG.info("Dashboard http://%s:%s; local trusted-network prototype", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if client:
            client.disconnect()
            client.loop_stop()


if __name__ == "__main__":
    main()
