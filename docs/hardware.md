# ESP32-WROOM-32 setup

Reference target: classic ESP32 DevKit/WROOM-32, Arduino core **3.0.7**, PubSubClient **2.8**, ArduinoJson **7.2.1**. This is not an ESP32-S3 pin assignment. No board was attached during authoring.

## Parts and wiring

| Part | Connection |
|---|---|
| 10 kΩ potentiometer end 1 | ESP32 3V3 |
| Potentiometer end 2 | ESP32 GND |
| Potentiometer wiper | GPIO34 / ADC1 |
| USB | Board power and Serial at 115200 |

Use 0–3.3 V only at the input; do not connect 5 V. The firmware reports raw 12-bit ADC counts, not calibrated voltage or temperature. GPIO34 is input-only. An unconnected pin floats; use the potentiometer or a defined ground input.

## Build

Install the Arduino ESP32 package URL `https://espressif.github.io/arduino-esp32/package_esp32_index.json`, the pinned core/libraries above, then copy `config.example.h` to `config.h` alongside the sketch. Set a unique device ID, your Wi-Fi credentials and broker LAN address locally. `config.h` is ignored by git. CI builds with placeholder values only.

```sh
arduino-cli core update-index --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32@3.0.7 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli lib install PubSubClient@2.8 ArduinoJson@7.2.1
cp firmware/device_monitor/config.example.h firmware/device_monitor/config.h
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/device_monitor
```

Select the actual serial port for uploading; no automatic upload is performed here.

## First filesystem provisioning

The firmware deliberately uses `LittleFS.begin(false)` and stops sampling if mounting fails. On a fresh board, provision LittleFS once with a separate minimal sketch calling `LittleFS.begin(true)` and checking success, then flash the monitor. **Formatting erases existing LittleFS contents; use a new board or back up its files first.** Choose a partition scheme with filesystem space, and keep it unchanged. A later mount failure must be diagnosed rather than silently reformatted. Verify Serial reports no storage errors.

## Broker connection

The default Compose broker is loopback-only and is intentionally inaccessible from an ESP32. First validate with the included simulator. For physical hardware use a separate authenticated broker on a trusted LAN; configure credentials/TLS and topic ACLs on both backend and firmware before wider deployment. The provided firmware transport is plaintext and has no credential support yet, so a temporary isolated lab broker is the only directly supported physical connection in v0.1. Restrict its firewall to the board/test host and remove the temporary listener after testing. Never port-forward it to the Internet.

## Hardware acceptance evidence still required

Record board/module revision, partition table, actual wiring photograph, tool versions, initial queue size, network outage duration, reconnect/ACK logs, power-cut trials, ADC values and queue overflow/drop counters. Do not change `DEVICE_ID`, erase NVS, or flash a new partition layout while preserving pending queue files. Unique identity and queue survival must be verified physically.
