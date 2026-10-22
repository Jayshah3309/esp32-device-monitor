"""Opt-in real-broker integration. Run with MQTT_TEST_HOST=127.0.0.1."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor import Store,mqtt_ingestion
from simulator import Outbox,mqtt_transport


@unittest.skipUnless(os.getenv('MQTT_TEST_HOST'),'requires real MQTT broker')
class MQTTTests(unittest.TestCase):
    def test_application_ack_and_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(str(Path(tmp)/'db'))
            box=Outbox(str(Path(tmp)/'outbox'))
            raw=dict(device_id='mqtt-test',boot_id='b1',seq=0,uptime_ms=0,metric='adc_raw',value=123,source='simulated',firmware='test')
            box.add(raw)
            host=os.environ['MQTT_TEST_HOST']
            backend=mqtt_ingestion(store,host,1883)
            device,send=mqtt_transport(box,host,1883,'mqtt-test')
            try:
                deadline=time.monotonic()+15
                while box.first() and time.monotonic()<deadline:
                    send(raw);time.sleep(.2)
                self.assertIsNone(box.first(),'no committed application ACK within 15s')
                device.publish('devices/mqtt-test/telemetry',json.dumps(raw),qos=1).wait_for_publish(timeout=3)
                time.sleep(.3)
                self.assertEqual(len(store.history()),1)
            finally:
                device.disconnect();device.loop_stop();backend.disconnect();backend.loop_stop()


if __name__=='__main__':unittest.main()
