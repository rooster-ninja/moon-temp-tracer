#include <Arduino.h>
#include <RadioLib.h>

#define PIN_CS        41
#define PIN_DIO1      39
#define PIN_RESET     42
#define PIN_BUSY      40
#define PIN_RF_SWITCH 38
#define PIN_SCK       7
#define PIN_MISO      8
#define PIN_MOSI      9

#ifndef NODE_ID
#define NODE_ID 0x00
#endif

#define LOGI(fmt, ...) Serial.printf("[INFO]  " fmt "\n", ##__VA_ARGS__)
#define LOGW(fmt, ...) Serial.printf("[WARN]  " fmt "\n", ##__VA_ARGS__)
#define LOGE(fmt, ...) Serial.printf("[ERROR] " fmt "\n", ##__VA_ARGS__)
#define LOGD(fmt, ...) Serial.printf("[DEBUG] " fmt "\n", ##__VA_ARGS__)

SX1262 radio = new Module(PIN_CS, PIN_DIO1, PIN_RESET, PIN_BUSY);

#define FRAME_SIZE 20

#define FRAME_STEPPER_CONTROL 0x01
#define FRAME_DATA_REQUEST    0x02
#define FRAME_DATA_RESPONSE   0x03
#define FRAME_ACK             0x04

uint8_t seqCounter = 0;
volatile bool receivedFlag = false;

void setFlag() {
  receivedFlag = true;
}

void setTarget(float az, float alt) {
  LOGI("[STUB] setTarget az=%.3f alt=%.3f", az, alt);
}

void readADCChannels(float *out, uint8_t count) {
  static float fake = 0.0;
  for (uint8_t i = 0; i < count; i++) out[i] = fake + i;
  fake += 0.1;
  LOGD("readADCChannels: generated %d fake values, base=%.3f", count, fake);
}

// Emits a machine-parseable line for the Pi-side Reticulum bridge to pick up
void sendToReticulum(uint8_t sourceId, float *values, uint8_t count) {
  Serial.print("RNS:{\"source\":");
  Serial.print(sourceId);
  Serial.print(",\"values\":[");
  for (uint8_t i = 0; i < count; i++) {
    Serial.print(values[i], 3);
    if (i < count - 1) Serial.print(",");
  }
  Serial.println("]}");
}

void sendFrame(uint8_t buf[FRAME_SIZE]) {
  radio.standby();
  delay(10);
  int state = radio.transmit(buf, FRAME_SIZE);
  if (state != RADIOLIB_ERR_NONE) {
    LOGE("Transmit failed, code=%d", state);
  }
  radio.startReceive();
}

void sendStepperControl(float az, float alt) {
  uint8_t buf[FRAME_SIZE] = {0};
  buf[0] = FRAME_STEPPER_CONTROL;
  buf[1] = NODE_ID;
  memcpy(&buf[2], &az, 4);
  memcpy(&buf[6], &alt, 4);
  sendFrame(buf);
  LOGI("Sent STEPPER_CONTROL az=%.3f alt=%.3f", az, alt);
}

void sendDataRequest() {
  uint8_t buf[FRAME_SIZE] = {0};
  buf[0] = FRAME_DATA_REQUEST;
  buf[1] = NODE_ID;
  sendFrame(buf);
  LOGI("Sent DATA_REQUEST");
}

void sendDataResponse() {
  float channels[4];
  readADCChannels(channels, 4);
  uint8_t buf[FRAME_SIZE] = {0};
  buf[0] = FRAME_DATA_RESPONSE;
  buf[1] = NODE_ID;
  buf[2] = 4;
  memcpy(&buf[3], channels, sizeof(channels));
  sendFrame(buf);
  LOGI("Sent DATA_RESPONSE (4 channels)");
}

void sendAck(uint8_t seq) {
  uint8_t buf[FRAME_SIZE] = {0};
  buf[0] = FRAME_ACK;
  buf[1] = NODE_ID;
  buf[2] = seq;
  sendFrame(buf);
  LOGI("Sent ACK seq=%d", seq);
}

void handleFrame(uint8_t data[FRAME_SIZE]) {
  uint8_t type = data[0];
  uint8_t sourceId = data[1];
  LOGD("handleFrame: type=0x%02X sourceId=0x%02X", type, sourceId);

  switch (type) {
    case FRAME_STEPPER_CONTROL: {
      float az, alt;
      memcpy(&az, &data[2], 4);
      memcpy(&alt, &data[6], 4);
      LOGI("Received STEPPER_CONTROL from 0x%02X az=%.3f alt=%.3f", sourceId, az, alt);
      setTarget(az, alt);
      sendAck(seqCounter++);
      break;
    }
    case FRAME_DATA_REQUEST: {
      LOGI("Received DATA_REQUEST from 0x%02X", sourceId);
      sendDataResponse();
      break;
    }
    case FRAME_DATA_RESPONSE: {
      uint8_t count = data[2];
      if (count > 4) count = 4;
      float values[4];
      memcpy(values, &data[3], count * sizeof(float));
      String vals = "";
      for (uint8_t i = 0; i < count; i++) {
        vals += String(values[i], 3) + " ";
      }
      LOGI("Received DATA_RESPONSE from 0x%02X: %s", sourceId, vals.c_str());
      sendToReticulum(sourceId, values, count);
      break;
    }
    case FRAME_ACK: {
      LOGI("Received ACK from 0x%02X seq=%d", sourceId, data[2]);
      break;
    }
    default: {
      LOGW("Unknown frame type 0x%02X from 0x%02X", type, sourceId);
      break;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  LOGI("Boot: starting setup(), NODE_ID=0x%02X", NODE_ID);

  pinMode(PIN_RF_SWITCH, OUTPUT);
  digitalWrite(PIN_RF_SWITCH, HIGH);

  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);

  int state = radio.begin(915.0, 125.0, 8, 5, RADIOLIB_SX126X_SYNC_WORD_PRIVATE, 17);
  if (state != RADIOLIB_ERR_NONE) {
    LOGE("Radio init FAILED, code=%d", state);
    while (true) delay(1000);
  }
  LOGI("Radio initialized successfully.");

  radio.setDio1Action(setFlag);
  radio.startReceive();

#ifdef ROLE_GATEWAY
  LOGI("Role: GATEWAY");
#else
  LOGI("Role: FIELD");
#endif
}

void loop() {
  if (receivedFlag) {
    receivedFlag = false;
    uint8_t buf[FRAME_SIZE];
    int state = radio.readData(buf, FRAME_SIZE);
    if (state == RADIOLIB_ERR_NONE) {
      LOGD("Packet received, RSSI=%.1f, SNR=%.1f", radio.getRSSI(), radio.getSNR());
      handleFrame(buf);
    } else {
      LOGW("readData() returned code=%d", state);
    }
    radio.startReceive();
  }

#ifdef ROLE_GATEWAY
  static unsigned long lastSend = 0;
  if (millis() - lastSend > 3000) {
    lastSend = millis();
    static float az = 0.0;
    az += 1.0;
    sendStepperControl(az, 45.0);
    delay(200);
    sendDataRequest();
  }
#else
  static unsigned long lastHeartbeat = 0;
  if (millis() - lastHeartbeat > 2000) {
    lastHeartbeat = millis();
    LOGI("Field node alive, waiting for frames...");
  }
#endif
}
