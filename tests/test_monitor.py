import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import Store, handler, validate
from simulator import Outbox


def record(seq=0):
    return dict(device_id='test-01', boot_id='boot-1', seq=seq, uptime_ms=1000,
                metric='temperature_c', value=24.5, source='simulated', firmware='test-1')


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = os.getenv('TEST_DATABASE_URL', str(Path(self.temp.name)/'readings.sqlite'))
        self.store = Store(self.database)
        if self.store.postgres:
            with self.store.connection() as c:
                c.execute('DELETE FROM readings')  # dedicated disposable CI database only

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, data):
        return self.store.ingest(json.dumps(data).encode())

    def test_commit_survives_reopening_store(self):
        self.assertEqual(self.ingest(record())['status'], 'stored')
        self.assertEqual(len(Store(self.database).history()), 1)

    def test_duplicate_is_idempotent(self):
        first = self.ingest(record())
        received = self.store.history()[0]['received_at']
        self.assertEqual(self.ingest(record())['status'], 'duplicate')
        self.assertEqual(len(self.store.history()), 1)
        self.assertEqual(self.store.history()[0]['received_at'], received)
        self.assertEqual(first['seq'], 0)

    def test_identity_collision_does_not_overwrite(self):
        self.ingest(record())
        with self.assertRaises(ValueError):
            self.ingest(dict(record(), value=99))
        self.assertEqual(self.store.history()[0]['value'], 24.5)

    def test_concurrent_duplicates(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.ingest(record()), range(20)))
        self.assertEqual(sum(r['status']=='stored' for r in results), 1)

    def test_invalid_inputs(self):
        invalid = [b'[]', b'null', b'{', b'\xff', b'x'*1025]
        for change in [dict(seq=True), dict(value=float('nan')),dict(value=True),dict(value=201),
                       dict(device_id='../bad'),dict(boot_id=''),dict(seq=-1),dict(uptime_ms=2**63),
                       dict(metric='unknown'),dict(source='fake'),dict(extra=1)]:
            invalid.append(json.dumps(dict(record(), **change)).encode())
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate(raw)

    def test_topic_binding(self):
        with self.assertRaises(ValueError):
            self.store.ingest(json.dumps(record()).encode(), 'devices/other/telemetry')
        self.assertEqual(self.store.history(), [])

    def test_device_is_stale_after_threshold(self):
        self.ingest(record())
        with self.store.connection() as c:
            c.execute("UPDATE readings SET received_at='2020-01-01T00:00:00+00:00'")
        self.assertEqual(self.store.devices()[0]['status'], 'stale')

    def test_history_filter_and_limit(self):
        for n in range(3): self.ingest(record(n))
        self.assertEqual([r['seq'] for r in self.store.history('test-01',2)], [2,1])
        self.assertEqual(self.store.history('missing'), [])

    def test_http_routes_and_errors(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),handler(self.store))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base+'/') as r:
                self.assertIn(b'Device Monitor',r.read())
            with urlopen(Request(base+'/api/telemetry',json.dumps(record()).encode(),{'Content-Type':'application/json'})) as r:
                self.assertEqual(json.load(r)['status'],'stored')
            with urlopen(base+'/api/devices') as r:
                self.assertEqual(json.load(r)[0]['device_id'],'test-01')
            for path in ['/api/readings?limit=501','/api/readings?limit=no','/api/readings?device_id=..']:
                with self.assertRaises(HTTPError) as error: urlopen(base+path)
                self.assertEqual(error.exception.code,400)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base+'/api/telemetry',b'[]'))
            self.assertEqual(error.exception.code,400)
        finally:
            server.shutdown();server.server_close();thread.join()


class OutboxTests(unittest.TestCase):
    def test_persistence_capacity_and_ack_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp)/'queue.sqlite')
            box = Outbox(path, capacity=2)
            self.assertTrue(box.add(record(0)))
            self.assertTrue(box.add(record(1)))
            self.assertFalse(box.add(record(2)))
            box = Outbox(path, capacity=2)
            self.assertEqual(box.first()['seq'],0)
            ack = dict(device_id='test-01',boot_id='boot-1',seq=0,status='stored')
            for bad in [dict(ack,seq=1),dict(ack,seq=False),dict(ack,boot_id='wrong'),dict(ack,status='failed')]:
                self.assertFalse(box.acknowledge(bad))
            self.assertTrue(box.acknowledge(ack))
            self.assertEqual(box.first()['seq'],1)
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute("SELECT value FROM counters WHERE name='dropped'").fetchone()[0],1)

    def test_lost_ack_replay_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, box = Store(str(Path(tmp)/'db')),Outbox(str(Path(tmp)/'outbox'))
            box.add(record())
            raw = json.dumps(box.first()).encode()
            store.ingest(raw)  # simulate connection loss after database commit, before ACK
            ack = store.ingest(raw)
            self.assertEqual(ack['status'],'duplicate')
            self.assertTrue(box.acknowledge(ack))
            self.assertIsNone(box.first())
            self.assertEqual(len(store.history()),1)


if __name__ == '__main__': unittest.main()
