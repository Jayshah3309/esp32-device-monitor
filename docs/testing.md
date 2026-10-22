# Verification and evidence

## Authoring status — 22 October 2026

Final local Python 3.12 run: **13 tests passed**, including a real Mosquitto 2.0.18 broker and a CLI simulator delivering five versioned records. Dashboard JavaScript syntax also passed `node --check`. The initial CLI experiment exposed rejection of dotted firmware versions; validation was corrected and a CLI regression test added. No hardware results, performance numbers or OTA claims are implied.

Initial [GitHub CI run](https://github.com/Jayshah3309/esp32-device-monitor/actions/runs/39771946477), commit `3e27ae67caa0d6c4e4d6eab2a169e64e531667d2`, passed Python 3.10/3.12, PostgreSQL, real MQTT and ESP32 compilation. That run predates the version-validation fix; the final run is recorded below once verified. The runner emitted Node 20 deprecation and Ubuntu runner migration notices; jobs passed. Docker Compose itself has not been executed in the authoring environment.

## Reproduce

```sh
python -m unittest discover -s tests -v
python -m pip install -r requirements.txt
```

With a local Mosquitto listener on port 1883:

```sh
MQTT_TEST_HOST=127.0.0.1 python -m unittest discover -s tests -p test_mqtt.py -v
```

With a **disposable test database** (tests delete all rows in its `readings` table):

```sh
TEST_DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1/monitor_test python -m unittest discover -s tests -p test_monitor.py -v
```

Environment assignment above uses POSIX shell syntax; in PowerShell use `$env:MQTT_TEST_HOST="127.0.0.1"` (and similarly `TEST_DATABASE_URL`) before running Python. Never point tests at a real telemetry database.

CI runs Python 3.10/3.12, SQLite tests, PostgreSQL tests, real-broker message/ACK integration, and ESP32 target compilation. A workflow file is not a passing result until its actual run is inspected.

## Automated coverage

- Database reopen/persistence and concurrent duplicate ingestion.
- Identical replay versus conflicting identity reuse.
- Strict payload types, finite/range validation and topic/device binding.
- History filtering, result bounds, staleness and HTTP errors.
- Outbox persistence, capacity, wrong ACK rejection and lost-ACK replay.
- Optional actual MQTT publish → commit → ACK → queue removal and duplicate replay.

## Manual experiments, results pending

| Experiment | Procedure | Required evidence |
|---|---|---|
| Backend outage | Run simulator while backend is stopped, then restart backend | Initial/final queue count, accepted/dropped counts, DB uniqueness |
| Broker outage | Stop broker while devices queue, then restore | Logs, time to first successful commit/ACK, dropped count |
| Process restart | Restart simulator using same outbox | Old boot records drain before new records |
| Device power cut | Interrupt actual ESP32 power during enqueue/retry | Recovered files, missing/corrupt entries, NVS identity |
| Full queue | Leave destination unavailable beyond 32 samples | Existing FIFO preserved, new drops reported |
| Sustained run | Record hardware/software configuration and duration | Memory, latency distribution, storage growth and failures |

No zero-loss guarantee: uncommitted samples, exhausted capacity, disk failure and device storage faults can lose data. A corrupt firmware queue entry halts firmware processing rather than silently discarding evidence. The host simulator and ESP32 use different queue implementations; host tests do not validate LittleFS.
