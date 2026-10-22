# ESP32 Device Monitor

A device-to-dashboard reference project focused on **delivery evidence and recovery**. An ESP32 or clearly labeled synthetic device queues readings, sends them to an MQTT ingestor, and removes them only after an application acknowledgement issued after a database commit.

Newly authored with AI assistance for Jay Shah's embedded systems portfolio. This is a local engineering prototype, not a deployed fleet service or evidence of physical hardware validation.

## What works in this version

- Python telemetry validator and HTTP API; SQLite quick start and PostgreSQL adapter.
- MQTT ingestion with device/topic binding and application acknowledgements.
- Idempotent storage keyed by `(device_id, boot_id, seq)`; conflicting reuse is rejected.
- Durable simulator outbox with a 32-record bound, retry, and explicit drop-new policy.
- Dashboard with source labels, latest readings, freshness, per-device history and chart.
- ESP32-WROOM-32 reference firmware: GPIO34 ADC, LittleFS queue, boot identity in NVS, reconnect and retry.
- Docker Compose recipe for PostgreSQL, Mosquitto, backend and synthetic device.
- Automated persistence, duplicate, API and failure-recovery tests; optional real MQTT and PostgreSQL tests.

## Try it without buying hardware

Requires Python 3.10+; this path uses only the standard library.

```sh
python monitor.py
```

In another terminal in the same directory:

```sh
python simulator.py
```

Open **http://127.0.0.1:8080**. The synthetic temperature signal is labeled `simulated`; it is not a physical measurement. Stop with Ctrl+C. Readings and pending queue entries remain under `data/`.

## Full MQTT/PostgreSQL stack

Requires Docker Engine and Compose:

```sh
docker compose up --build
```

Open the same dashboard address. Stop using `docker compose down`; named volumes preserve database and simulator outbox. **Do not add `-v` unless you intend to erase those volumes.** This recipe uses local demonstration credentials and publishes ports only on loopback.

## Tests

```sh
python -m unittest discover -s tests -v
```

See [testing and evidence](docs/testing.md) for real-broker/PostgreSQL runs and honest verification status. See [architecture and protocol](docs/architecture.md) for delivery semantics and [hardware setup](docs/hardware.md) before flashing.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Database availability |
| GET | `/api/devices` | Latest reading and server-receipt freshness per device |
| GET | `/api/readings?device_id=demo-01&limit=100` | History; limit 1–500 |
| POST | `/api/telemetry` | Local JSON demo ingestion; same validator as MQTT |

## Limits and next milestones

This release has **no TLS, authentication, tenant isolation, alerts, OTA updates, or OpenTelemetry integration**. Keep it on a trusted local machine/network. Backend retention is unbounded; export/retention and pagination precede deployment. A fresh reading is not proof that the sensor is correct or the device healthy. Offline catch-up is shown in receipt order, not a synchronized device timeline.

Next: measured broker outage/restart trials; actual ESP32 queue/power-loss testing; authenticated MQTT with topic ACLs; HTTPS OTA with self-test/rollback; resource and latency benchmarks. Those features are proposed, not claimed as implemented.

## Portfolio build sequence

1. **This project:** device monitoring and delivery reliability.
2. Embedded Fault Testing Lab: deeper controller fault injection.
3. Railway Inspection Platform: extend the existing railway application.
4. Waste sorter: instrument and publish measured sorting trials.
5. Motor condition monitoring: collect data before training/evaluating an anomaly model.

Do not claim completion of hardware experiments from software tests or compilation alone. License selection remains pending; dependency licenses remain with their respective authors.
