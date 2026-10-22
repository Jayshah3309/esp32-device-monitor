// ESP32-WROOM-32 reference. Trusted LAN only; ADC counts, not calibrated temperature.
#include <WiFi.h>
#include <PubSubClient.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include "config.h"

WiFiClient network;
PubSubClient mqtt(network);
Preferences prefs;
uint32_t bootNumber, sequenceNumber = 0, dropped = 0;
uint32_t lastSample, lastSend, lastConnect;
String bootId, activeFile, activeBoot;
uint32_t activeSeq;
bool storageReady = false;
const uint8_t MAX_QUEUE = 32;

bool elapsed(uint32_t now, uint32_t then, uint32_t interval) { return uint32_t(now-then) >= interval; }

String oldestFile(int &count) {
  count = 0;
  String oldest;
  File root = LittleFS.open("/");
  for (File file = root.openNextFile(); file; file = root.openNextFile()) {
    String path = String("/") + file.name();
    if (path.endsWith(".json")) {
      count++;
      if (oldest.isEmpty() || path < oldest) oldest = path;
    }
  }
  return oldest;
}

void onAck(char *topic, byte *payload, unsigned int length) {
  if (String(topic) != String("devices/") + DEVICE_ID + "/ack" || activeFile.isEmpty()) return;
  JsonDocument ack;
  if (deserializeJson(ack, payload, length)) return;
  String status = ack["status"] | "";
  if (status != "stored" && status != "duplicate") return;
  if (String(ack["device_id"] | "") != DEVICE_ID || String(ack["boot_id"] | "") != activeBoot) return;
  if (!ack["seq"].is<uint32_t>() || ack["seq"].as<uint32_t>() != activeSeq) return;
  if (LittleFS.remove(activeFile)) activeFile = "";
}

void sample(uint32_t now) {
  int count;
  oldestFile(count);
  if (count >= MAX_QUEUE) { dropped++; Serial.printf("queue full; dropped new=%lu\n", (unsigned long)dropped); return; }
  if (sequenceNumber == UINT32_MAX) { Serial.println("sequence exhausted; restart required"); storageReady=false; return; }
  JsonDocument data;
  data["device_id"] = DEVICE_ID;
  data["boot_id"] = bootId;
  data["seq"] = sequenceNumber;
  data["uptime_ms"] = now;
  data["metric"] = "adc_raw";
  data["value"] = analogRead(34);
  data["source"] = "hardware";
  data["firmware"] = "esp32-0.1.0";
  char name[40];
  snprintf(name,sizeof(name),"/%010lu_%010lu.json",(unsigned long)bootNumber,(unsigned long)sequenceNumber++);
  File file = LittleFS.open("/pending.tmp", "w");
  if (!file) { Serial.println("outbox write failed"); return; }
  size_t expected = measureJson(data);
  size_t written = serializeJson(data, file);
  file.flush(); file.close();
  if (written != expected || !LittleFS.rename("/pending.tmp",name)) {
    LittleFS.remove("/pending.tmp"); Serial.println("outbox commit failed");
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(34, INPUT);
  analogReadResolution(12);
  // Never silently format a failed filesystem. Provision explicitly using setup guide.
  if (!LittleFS.begin(false)) { Serial.println("LittleFS unavailable; see provisioning guide"); return; }
  if (!prefs.begin("monitor", false)) { Serial.println("NVS unavailable"); return; }
  bootNumber = prefs.getUInt("boot", 0);
  if (bootNumber == UINT32_MAX || prefs.putUInt("boot", bootNumber+1) != sizeof(uint32_t)) {
    Serial.println("cannot reserve boot identity"); return;
  }
  bootNumber++;
  prefs.end();
  bootId = String(bootNumber);
  LittleFS.remove("/pending.tmp"); // incomplete sample is not a committed queue entry
  storageReady = true;
  WiFi.mode(WIFI_STA); WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onAck);
  if (!mqtt.setBufferSize(768)) { storageReady=false; Serial.println("MQTT allocation failed"); }
  mqtt.setSocketTimeout(2);
  lastSample=millis(); lastSend=millis()-5000; lastConnect=millis()-5000;
}

void loop() {
  if (!storageReady) { delay(100); return; }
  uint32_t now=millis();
  if (elapsed(now,lastConnect,5000)) {
    lastConnect=now;
    if (WiFi.status()!=WL_CONNECTED) WiFi.reconnect();
    else if (!mqtt.connected()) {
      String clientId=String("node-")+DEVICE_ID;
      if (mqtt.connect(clientId.c_str())) {
        String topic=String("devices/")+DEVICE_ID+"/ack";
        if (!mqtt.subscribe(topic.c_str(),1)) mqtt.disconnect();
      }
    }
  }
  mqtt.loop();
  now=millis();
  if (elapsed(now,lastSample,2000)) { lastSample=now; sample(now); }
  if (mqtt.connected() && elapsed(now,lastSend,5000)) {
    lastSend=now;
    int count;
    activeFile=oldestFile(count);
    if (!activeFile.isEmpty()) {
      File file=LittleFS.open(activeFile,"r");
      String raw=file.readString(); file.close();
      JsonDocument data;
      if (deserializeJson(data,raw)) { Serial.println("corrupt queued record; inspect storage"); storageReady=false; return; }
      activeBoot=String(data["boot_id"] | ""); activeSeq=data["seq"].as<uint32_t>();
      String topic=String("devices/")+DEVICE_ID+"/telemetry";
      mqtt.publish(topic.c_str(),raw.c_str(),false); // retain on failure or missing application ACK
    }
  }
  delay(5);
}
