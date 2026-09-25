#include <Arduino.h>
#include <RadioLib.h>
#include <math.h>

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

// Mount limits, enforced here as a firmware-side safety net in addition to
// the clamping already done upstream (moon_downlink_daemon.py on the
// server). Elevation actuator is a hard 0-90 range; azimuth actuator only
// sweeps a 180 degree arc, not a full circle.
#define ALT_MIN 0.0f
#define ALT_MAX 90.0f
#define AZ_MIN  0.0f
#define AZ_MAX  180.0f

// --- Field node stepper calibration (TODO, see TODO.md) ---------------
//
// These are placeholders. The field node - not the gateway, not the
// server - is the single source of truth for its own kinematics, since
// it's the thing that gets physically re-homed/re-geared. Everything
// below needs real values once the stepper hardware is up:
//   - STEPS_PER_DEG_AZ/ALT: derived from motor full steps/rev, driver
//     microstepping, and gear/belt ratio.
//   - AZ_HOME_OFFSET/ALT_HOME_OFFSET: degrees between the homing
//     position (wherever the limit switches/homing routine parks the
//     axis) and az=0/alt=0.
//   - FIELD_AZ_MIN/MAX, FIELD_ALT_MIN/MAX: the mount's *actual* calibrated
//     travel, from homing/limit switches - may not exactly match the
//     generic AZ_MIN/MAX, ALT_MIN/MAX above, which are only a nominal
//     upstream assumption. This clamp is the real, authoritative one.
#define STEPS_PER_DEG_AZ   1.0f   // TODO: replace with real gearing-derived value
#define STEPS_PER_DEG_ALT  1.0f   // TODO: replace with real gearing-derived value
#define AZ_HOME_OFFSET_DEG  0.0f  // TODO: set from homing routine
#define ALT_HOME_OFFSET_DEG 0.0f  // TODO: set from homing routine
#define FIELD_AZ_MIN  AZ_MIN   // TODO: replace with real homed limit
#define FIELD_AZ_MAX  AZ_MAX   // TODO: replace with real homed limit
#define FIELD_ALT_MIN ALT_MIN  // TODO: replace with real homed limit
#define FIELD_ALT_MAX ALT_MAX  // TODO: replace with real homed limit

void setFlag() {
  receivedFlag = true;
}

// TODO(stepper-hardware): stub until the stepper driver library/pins are
// chosen (see TODO.md). Converts a calibrated step target into physical
// motion. For now this just logs what it would do.
void moveStepperTo(long azSteps, long altSteps) {
  LOGI("[STUB] moveStepperTo azSteps=%ld altSteps=%ld", azSteps, altSteps);
}

// TODO(stepper-hardware): real calibration (steps/degree, home offsets,
// homed limits) isn't wired up yet - see TODO.md. This clamps against
// placeholder limits and converts using placeholder scale factors so the
// call shape is right; swap the constants above for real ones and this
// should just work.
void setTarget(float az, float alt) {
  if (az < FIELD_AZ_MIN || az > FIELD_AZ_MAX) {
    LOGW("setTarget: az=%.3f outside calibrated limits [%.1f,%.1f], clamping", az, FIELD_AZ_MIN, FIELD_AZ_MAX);
    az = az < FIELD_AZ_MIN ? FIELD_AZ_MIN : FIELD_AZ_MAX;
  }
  if (alt < FIELD_ALT_MIN || alt > FIELD_ALT_MAX) {
    LOGW("setTarget: alt=%.3f outside calibrated limits [%.1f,%.1f], clamping", alt, FIELD_ALT_MIN, FIELD_ALT_MAX);
    alt = alt < FIELD_ALT_MIN ? FIELD_ALT_MIN : FIELD_ALT_MAX;
  }

  long azSteps = lroundf((az + AZ_HOME_OFFSET_DEG) * STEPS_PER_DEG_AZ);
  long altSteps = lroundf((alt + ALT_HOME_OFFSET_DEG) * STEPS_PER_DEG_ALT);

  LOGI("setTarget az=%.3f alt=%.3f -> azSteps=%ld altSteps=%ld", az, alt, azSteps, altSteps);
  moveStepperTo(azSteps, altSteps);
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
  if (az < AZ_MIN || az > AZ_MAX) {
    LOGW("Clamping az=%.3f to mount arc [%.1f,%.1f]", az, AZ_MIN, AZ_MAX);
    az = az < AZ_MIN ? AZ_MIN : AZ_MAX;
  }
  if (alt < ALT_MIN || alt > ALT_MAX) {
    LOGW("Clamping alt=%.3f to mount range [%.1f,%.1f]", alt, ALT_MIN, ALT_MAX);
    alt = alt < ALT_MIN ? ALT_MIN : ALT_MAX;
  }

  uint8_t buf[FRAME_SIZE] = {0};
  buf[0] = FRAME_STEPPER_CONTROL;
  buf[1] = NODE_ID;
  memcpy(&buf[2], &az, 4);
  memcpy(&buf[6], &alt, 4);
  sendFrame(buf);
  LOGI("Sent STEPPER_CONTROL az=%.3f alt=%.3f", az, alt);
}

#ifdef ROLE_GATEWAY
// Reads "SET:<az>,<alt>\n" lines from the Pi bridge over serial and
// relays them to the field node as STEPPER_CONTROL frames. Non-blocking:
// accumulates into a line buffer across loop() iterations.
#define SERIAL_CMD_BUF_LEN 64
char serialCmdBuf[SERIAL_CMD_BUF_LEN];
uint8_t serialCmdLen = 0;

void handleSerialLine(const char *line) {
  float az, alt;
  if (sscanf(line, "SET:%f,%f", &az, &alt) == 2) {
    LOGI("Serial SET received az=%.3f alt=%.3f", az, alt);
    sendStepperControl(az, alt);
  } else {
    LOGW("Unrecognized serial command: %s", line);
  }
}

void pollSerialCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialCmdLen > 0) {
        serialCmdBuf[serialCmdLen] = '\0';
        handleSerialLine(serialCmdBuf);
        serialCmdLen = 0;
      }
    } else if (serialCmdLen < SERIAL_CMD_BUF_LEN - 1) {
      serialCmdBuf[serialCmdLen++] = c;
    } else {
      // Line too long, drop it to resync on the next newline.
      serialCmdLen = 0;
    }
  }
}
#endif

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
      // Only the gateway sends this - a field node hearing one is a real
      // command; a gateway hearing one is its own transmission bouncing
      // back (RF self-reception), not a peer. Acting on it there wastes
      // airtime replying to itself and corrupts the log with fake activity.
#ifndef ROLE_GATEWAY
      float az, alt;
      memcpy(&az, &data[2], 4);
      memcpy(&alt, &data[6], 4);
      LOGI("Received STEPPER_CONTROL from 0x%02X az=%.3f alt=%.3f", sourceId, az, alt);
      setTarget(az, alt);
      sendAck(seqCounter++);
#else
      LOGD("Ignoring STEPPER_CONTROL from 0x%02X (gateway-only sends this - self-echo)", sourceId);
#endif
      break;
    }
    case FRAME_DATA_REQUEST: {
      // Only the gateway sends this - see FRAME_STEPPER_CONTROL above.
#ifndef ROLE_GATEWAY
      LOGI("Received DATA_REQUEST from 0x%02X", sourceId);
      sendDataResponse();
#else
      LOGD("Ignoring DATA_REQUEST from 0x%02X (gateway-only sends this - self-echo)", sourceId);
#endif
      break;
    }
    case FRAME_DATA_RESPONSE: {
      // Only the field node sends this - a gateway hearing one is a real
      // reply; a field node hearing one is its own echo.
#ifdef ROLE_GATEWAY
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
#else
      LOGD("Ignoring DATA_RESPONSE from 0x%02X (field-only sends this - self-echo)", sourceId);
#endif
      break;
    }
    case FRAME_ACK: {
      // Only the field node sends this - see FRAME_DATA_RESPONSE above.
#ifdef ROLE_GATEWAY
      LOGI("Received ACK from 0x%02X seq=%d", sourceId, data[2]);
#else
      LOGD("Ignoring ACK from 0x%02X (field-only sends this - self-echo)", sourceId);
#endif
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
  pollSerialCommands();

  static unsigned long lastDataRequest = 0;
  if (millis() - lastDataRequest > 3000) {
    lastDataRequest = millis();
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
