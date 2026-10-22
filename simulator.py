"""Synthetic sensor with a bounded, durable SQLite outbox and application ACKs."""
import argparse
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import URLError


class Outbox:
    def __init__(self, path, capacity=32):
        self.path, self.capacity, self.lock = path, capacity, threading.Lock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL)")
            c.execute("INSERT OR IGNORE INTO counters VALUES ('dropped',0)")

    def add(self, data):
        with self.lock, sqlite3.connect(self.path) as c:
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT COUNT(*) FROM queue").fetchone()[0] >= self.capacity:
                c.execute("UPDATE counters SET value=value+1 WHERE name='dropped'")
                return False  # preserve existing records; drop new sample
            c.execute("INSERT INTO queue(payload) VALUES (?)", (json.dumps(data),))
            return True

    def first(self):
        with self.lock, sqlite3.connect(self.path) as c:
            row = c.execute("SELECT payload FROM queue ORDER BY id LIMIT 1").fetchone()
            return json.loads(row[0]) if row else None

    def acknowledge(self, ack):
        with self.lock, sqlite3.connect(self.path) as c:
            row = c.execute("SELECT id,payload FROM queue ORDER BY id LIMIT 1").fetchone()
            if not row or not isinstance(ack, dict) or ack.get('status') not in ('stored', 'duplicate'):
                return False
            data = json.loads(row[1])
            if any(ack.get(k) != data[k] or type(ack.get(k)) != type(data[k]) for k in ('device_id','boot_id','seq')):
                return False
            c.execute("DELETE FROM queue WHERE id=?", (row[0],))
            return True


def mqtt_transport(box, host, port, device):
    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sim-" + device)
    ready = threading.Event()
    def connected(client, userdata, flags, reason, props):
        if reason == 0:
            client.subscribe(f"devices/{device}/ack", qos=1)
    def subscribed(client, userdata, mid, reasons, props):
        if reasons and all(not r.is_failure for r in reasons):
            ready.set()
    def disconnected(client, userdata, flags, reason, props):
        ready.clear()
    def message(client, userdata, msg):
        try:
            box.acknowledge(json.loads(msg.payload))
        except (ValueError, UnicodeError):
            pass
    c.on_connect, c.on_subscribe, c.on_disconnect, c.on_message = connected, subscribed, disconnected, message
    c.reconnect_delay_set(1, 10)
    c.connect_async(host, port, 30)
    c.loop_start()
    def send(data):
        if ready.is_set():
            c.publish(f"devices/{device}/telemetry", json.dumps(data), qos=1)
    return c, send


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='http://127.0.0.1:8080')
    p.add_argument('--mqtt-host')
    p.add_argument('--mqtt-port', type=int, default=1883)
    p.add_argument('--device', default='demo-01')
    p.add_argument('--outbox', default='data/simulator.sqlite')
    p.add_argument('--interval', type=float, default=2)
    p.add_argument('--samples', type=int, default=0, help='0 runs until interrupted')
    args = p.parse_args()
    if args.interval <= 0 or args.samples < 0:
        p.error('interval must be positive and samples nonnegative')
    box, boot, start, seq = Outbox(args.outbox), uuid.uuid4().hex, time.monotonic(), 0
    client = None
    def send(data):
        req = Request(args.url + '/api/telemetry', json.dumps(data).encode(), {'Content-Type':'application/json'})
        try:
            with urlopen(req, timeout=2) as response:
                box.acknowledge(json.load(response))
        except (URLError, TimeoutError, ValueError):
            pass  # retain until server accepts the identical record
    if args.mqtt_host:
        client, send = mqtt_transport(box, args.mqtt_host, args.mqtt_port, args.device)
    next_sample, next_send = start, start
    try:
        while True:
            now = time.monotonic()
            if now >= next_sample and (args.samples == 0 or seq < args.samples):
                data = dict(device_id=args.device, boot_id=boot, seq=seq, uptime_ms=int((now-start)*1000),
                    metric='temperature_c', value=round(24+2*math.sin(seq/8),2), source='simulated', firmware='sim-0.1.0')
                print(json.dumps({'queued':box.add(data), 'seq':seq}), flush=True)
                seq += 1
                next_sample = now + args.interval
            if now >= next_send:
                first = box.first()
                if first:
                    send(first)
                next_send = now + 0.5
            if args.samples and seq >= args.samples and box.first() is None:
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if client:
            client.disconnect()
            client.loop_stop()


if __name__ == '__main__':
    main()
