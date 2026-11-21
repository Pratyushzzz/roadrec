// RoadRec firmware - XIAO ESP32-S3 (Sense)
//
//   core 1: sampler task  - reads IMU every 10 ms, GPS speed, feeds the roadcore Detector
//   core 0: writer task   - saves a finished event window to the SD card
//
// Wiring (3.3 V logic):
//   MPU6050  SDA -> D4 (GPIO5)   SCL -> D5 (GPIO6)   VCC -> 3V3   GND -> GND
//   GPS      TX  -> D7 (GPIO44)  RX  <- D6 (GPIO43)  VCC -> 3V3 or 5V per module   GND -> GND
//   SD       the slot on the Sense expansion board (CS = GPIO21, SPI SCK 7 / MISO 8 / MOSI 9)
// Do not drive GPIO21 as an LED: on this board it is the SD chip select.
//
// NOT yet tested on real hardware - see README.md for what to check first.
#include <Arduino.h>
#include <Wire.h>
#include <atomic>
#include <vector>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/message_buffer.h"
#include "roadcore.h"
#include "imu.h"
#include "gps.h"
#include "storage.h"
#include "rawlog.h"
#include "replay.h"
#include "roadrec_serial_export.h"

#define PIN_SDA 5
#define PIN_SCL 6
#define PIN_GPS_RX 44   // board RX  <- GPS TX
#define PIN_GPS_TX 43   // board TX  -> GPS RX
#define PIN_SD_CS 21
#define PIN_SD_SCK 7
#define PIN_SD_MISO 8
#define PIN_SD_MOSI 9

// ---- bench tests: need no IMU and no GPS (flip one at a time, rebuild, upload) ----
#define REPLAY_TEST 0         // 1 = play CSVs from /replay on the SD card through the detector instead of the IMU
#define REPLAY_SPEEDUP 1      // replay samples per 10 ms tick (1 = real time; 5 = five times faster)
#define GPS_LOOPBACK_TEST 0   // 1 = send fake NMEA on D6 and read it back on D7 (needs a jumper wire D6 <-> D7)

// Save every event while collecting data; set to false later to keep only alert-class events.
#define SAVE_ALL_EVENTS true

// Optional: a pin that goes LOW when vehicle power is lost (comparator on the supply).
// Define it to save a partial window while the reserve battery is still holding the board up.
// #define POWER_FAIL_PIN 2

static Imu imu;
static Gps gps;
static Storage store;
static rr::Detector* det = nullptr;
static bool sdOk = false;           // false = run without saving (bring-up without an SD card)
static bool imuOk = false;          // true once the IMU answered at boot and while reads keep working
static uint32_t imuFailStreak = 0;  // consecutive failed reads

// Continuous raw log of every sample to /raw/raw_NNNN.csv (needs the SD card). Used to record
// ordinary riding for the false-trigger test and for vibration measurements.
#define RAW_LOG true
#define SD_TEST_NO_IMU 0   // 1 = keep running without an IMU (SD test only). Set to 0 once the IMU is wired.
static RawLog rawlog;
static MessageBufferHandle_t rawBuf = nullptr;   // sampler -> writer, whole samples only
static bool rawOn = false;
static uint32_t rawDropped = 0;     // samples lost because the writer fell >10 s behind

// Finished windows wait in one of NSLOTS buffers for the writer task. Events can now be only ~2 s
// apart (a queued trigger is captured right after the current one), so one buffer is not enough:
// an SD stall during one event must not make us lose a crash that follows it.
constexpr int NSLOTS = 3;
struct Slot {
  std::vector<rr::Sample> win;
  rr::Result res;
  uint32_t trigSample = 0;
  std::atomic<bool> busy{false};   // true from "filled" until the writer has finished saving it
};
static Slot slots[NSLOTS];
static QueueHandle_t writeQ;       // holds slot numbers in the order the events happened
static volatile uint32_t eventsSeen = 0, eventsSaved = 0, eventsDropped = 0, imuErrors = 0, lateTicks = 0, clipCount = 0, maxSaveMs = 0;
#if REPLAY_TEST
static Replay replay;
static std::string replayLabels;   // labels of the file being replayed, in order
#endif

// Called from the sampler task when the detector has a finished window.
static void handleEvent() {
  rr::Result r = det->finishEvent();
  eventsSeen++;
  Serial.printf("[event] %s | %s\n", rr::labelName(r.label), r.reason);
#if REPLAY_TEST
  if (!replayLabels.empty()) replayLabels += ",";
  replayLabels += rr::labelName(r.label);
#endif
  bool keep = (SAVE_ALL_EVENTS || rr::isAlert(r.label)) && sdOk;
  if (!keep) return;
  int idx = -1;
  for (int k = 0; k < NSLOTS; k++)
    if (!slots[k].busy.load()) { idx = k; break; }
  if (idx < 0) { eventsDropped++; Serial.println("[event] all write slots busy, event dropped"); return; }
  Slot& s = slots[idx];
  s.res = r;
  s.trigSample = det->triggerSampleCount();
  s.win.assign(det->window(), det->window() + det->windowLen());   // copy before the next freeze
  s.busy.store(true);
  uint8_t id = (uint8_t)idx;
  xQueueSend(writeQ, &id, 0);      // queue length == NSLOTS, so this cannot be full
}

// Shows what is really on the I2C bus, to tell "IMU not found" apart: nothing answers (wiring or power)
// versus something answers at another address. Runs once at boot, only when the IMU was not found.
static void i2cDiag() {
  Serial.println("[i2c] scanning 0x08..0x77");
  bool at68 = false, at69 = false;
  int found = 0;
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[i2c] device answers at 0x%02X\n", a);
      found++;
      if (a == 0x68) at68 = true;
      if (a == 0x69) at69 = true;
    }
  }
  if (!found) {
    Serial.println("[i2c] nothing answered. Check: SDA->D4, SCL->D5 (not swapped), VCC->3V3, GND, solder joints.");
    return;
  }
  if (at69 && !at68) Serial.println("[i2c] MPU6050 is at 0x69 (AD0 pin is high). Pull AD0 to GND or call imu.begin(Wire, 0x69).");
  for (uint8_t a = 0x68; a <= 0x69; a++) {
    if ((a == 0x68 && !at68) || (a == 0x69 && !at69)) continue;
    Wire.beginTransmission(a);
    Wire.write(0x75);                                  // WHO_AM_I
    if (Wire.endTransmission(false) != 0) continue;
    if (Wire.requestFrom((int)a, 1) == 1)
      Serial.printf("[i2c] 0x%02X WHO_AM_I = 0x%02X (genuine MPU6050 = 0x68; some clones differ and still work)\n", a, (unsigned)Wire.read());
  }
}

#if GPS_LOOPBACK_TEST
// Sends a fake $GPRMC sentence on the GPS TX pin. With a jumper from D6 to D7 the board receives
// its own sentences, which tests the UART pins and the NMEA parser with no GPS module.
// Every 5 s the speed steps: no fix, then 5, 10, 15, 20 m/s.
static float loopSent = -1.0f;
static void gpsLoopbackSend() {
  uint32_t step = (millis() / 5000) % 5;
  float v = step * 5.0f;
  loopSent = step ? v : -1.0f;
  char body[96];
  snprintf(body, sizeof body, "GPRMC,120000.00,%c,2837.0000,N,07724.0000,E,%.2f,0.0,071026,,,A",
           step ? 'A' : 'V', v / 0.514444f);
  uint8_t cs = 0;
  for (const char* p = body; *p; p++) cs ^= (uint8_t)*p;
  Serial1.printf("$%s*%02X\r\n", body, cs);
}
#endif

#if REPLAY_TEST
static bool writerIdle() {
  if (uxQueueMessagesWaiting(writeQ) > 0) return false;
  for (int k = 0; k < NSLOTS; k++)
    if (slots[k].busy.load()) return false;
  return true;
}

// Plays every /replay/*.csv through a fresh detector, in name order, one file after the other.
static void replayTask(void*) {
  std::vector<std::string> files = replay.list("/replay");
  Serial.printf("[replay] %u file(s) in /replay\n", (unsigned)files.size());
  if (files.empty()) {
    Serial.println("[replay] nothing to play: copy 100 Hz CSVs into /replay on the SD card");
    vTaskDelete(nullptr);
  }
  const TickType_t period = pdMS_TO_TICKS(1000 / rr::FS);
  for (const std::string& name : files) {
    while (!writerIdle()) vTaskDelay(pdMS_TO_TICKS(20));      // SD must have one user at a time
    rr::Sample* buf = nullptr;
    size_t n = 0;
    if (!replay.load("/replay/" + name, &buf, &n) || n < 500) {
      Serial.printf("[replay] skipping %s (could not read it, or under 5 s)\n", name.c_str());
      if (buf) free(buf);
      continue;
    }
    Serial.printf("[replay] %s: %u samples (%.0f s)\n", name.c_str(), (unsigned)n, n / (float)rr::FS);
    delete det;
    det = new rr::Detector();
    replayLabels.clear();
    TickType_t last = xTaskGetTickCount();
    size_t i = 0;
    while (i < n) {
      vTaskDelayUntil(&last, period);
      if (xTaskGetTickCount() - last > period) lateTicks++;
      for (int j = 0; j < REPLAY_SPEEDUP && i < n; j++, i++)
        if (det->push(buf[i])) handleEvent();
    }
    if (det->flush()) handleEvent();                          // file ended mid-event: same as a power-fail save
    free(buf);
    while (!writerIdle()) vTaskDelay(pdMS_TO_TICKS(20));
    Serial.printf("[replay-result] %s: %s\n", name.c_str(), replayLabels.c_str());
    replay.appendResult(name, replayLabels);
  }
  Serial.printf("[replay] DONE. events=%lu saved=%lu dropped=%lu late=%lu slowest_save=%lu ms\n",
                (unsigned long)eventsSeen, (unsigned long)eventsSaved, (unsigned long)eventsDropped,
                (unsigned long)lateTicks, (unsigned long)maxSaveMs);
  Serial.println("[replay] results are also in /replay_results.txt on the SD card");
  vTaskDelete(nullptr);
}
#endif

static void samplerTask(void*) {
  TickType_t last = xTaskGetTickCount();
  const TickType_t period = pdMS_TO_TICKS(1000 / rr::FS);
  float lastA[3] = {0, 0, 1}, lastW[3] = {0, 0, 0};
  uint32_t lastReport = millis();
#ifdef POWER_FAIL_PIN
  bool powerFlushed = false;
#endif
  for (;;) {
    vTaskDelayUntil(&last, period);
    if (xTaskGetTickCount() - last > period) lateTicks++;     // we fell behind

#if GPS_LOOPBACK_TEST
    if (det->samplesSeen() % 20 == 0) gpsLoopbackSend();       // 5 sentences per second
#endif
    gps.poll();
    float a[3], w[3];
    if (imuOk) {
      if (imu.read(a, w)) {
        imuFailStreak = 0;
        memcpy(lastA, a, sizeof a); memcpy(lastW, w, sizeof w);
        if (imu.clipped(a)) clipCount++;
      } else {
        imuErrors++;                                           // reuse the previous reading
        if (++imuFailStreak >= 100) {                          // about 1 s of nothing: stop hammering the bus
          imuOk = false;                                       // (a failed read costs ~18 ms and floods the log)
          Serial.println("[imu] LOST: 100 failed reads in a row. Not reading it any more; check wiring and reset.");
        }
      }
    }                                                          // no IMU: lastA/lastW stay at the fake 1 g flat

    rr::Sample s;
    memcpy(s.a, lastA, sizeof s.a);
    memcpy(s.w, lastW, sizeof s.w);
    s.speed = gps.speedMps();
    if (rawOn && xMessageBufferSend(rawBuf, &s, sizeof s, 0) == 0) rawDropped++;
    if (det->push(s)) handleEvent();

#ifdef POWER_FAIL_PIN
    if (!powerFlushed && digitalRead(POWER_FAIL_PIN) == LOW) {
      powerFlushed = true;
      if (det->flush()) handleEvent();                          // save whatever exists, now
    }
#endif

#if GPS_LOOPBACK_TEST
    if (det->samplesSeen() % 100 == 0) {
      float got = gps.hasFix() ? gps.speedMps() : -1.0f;
      Serial.printf("[gps-loop] sent %5.1f m/s   got %5.1f m/s   %s   (sentences ok: %lu)\n", loopSent, got,
                    fabsf(got - loopSent) < 0.15f ? "OK" : "MISMATCH (a step change in the last second is normal)",
                    (unsigned long)gps.sentences());
    }
#endif
    if (millis() - lastReport > 10000) {
      lastReport = millis();
      float amag = sqrtf(lastA[0] * lastA[0] + lastA[1] * lastA[1] + lastA[2] * lastA[2]);
      Serial.printf("[status] imu=%s samples=%lu |a|=%.3f g clip=%lu gps_fix=%d gps_sentences=%lu speed=%.1f events=%lu saved=%lu dropped=%lu imu_err=%lu late=%lu\n",
                    imuOk ? "ok" : "MISSING", (unsigned long)det->samplesSeen(), amag, (unsigned long)clipCount, gps.hasFix(),
                    (unsigned long)gps.sentences(), gps.hasFix() ? gps.speedMps() : -1.0f,
                    (unsigned long)eventsSeen, (unsigned long)eventsSaved, (unsigned long)eventsDropped,
                    (unsigned long)imuErrors, (unsigned long)lateTicks);
      if (rawOn) Serial.printf("[raw] lines=%lu dropped=%lu\n", (unsigned long)rawlog.lines(), (unsigned long)rawDropped);
    }
  }
}

static void writerTask(void*) {
  for (;;) {
    uint8_t id;
    if (xQueueReceive(writeQ, &id, pdMS_TO_TICKS(50)) == pdTRUE) {
      Slot& s = slots[id];
      uint32_t no = 0, t0 = millis();
      bool ok = store.save(s.win.data(), (int)s.win.size(), s.res, s.trigSample, &no);
      uint32_t ms = millis() - t0;
      if (ms > maxSaveMs) maxSaveMs = ms;
      if (ok) eventsSaved++;
      Serial.printf("[sd] event %lu %s in %lu ms (%d samples)\n", (unsigned long)no, ok ? "saved" : "WRITE FAILED",
                    (unsigned long)ms, (int)s.win.size());
      if (ok) {                          // send the saved file to the website over USB
        char name[48], path[72];
        snprintf(name, sizeof name, "evt_%04u_%s.csv", (unsigned)no, rr::labelName(s.res.label));
        snprintf(path, sizeof path, "/events/%s", name);
        rrSendEvent(path, name);
      }
      s.busy.store(false);
    }
        rrHandleSerialCommands();
    if (rawOn) {                      // same task as the event writer, so the SD card has one user
      rr::Sample rs;
      while (xMessageBufferReceive(rawBuf, &rs, sizeof rs, 0) == sizeof rs) rawlog.add(rs);
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);     // never let a missing USB host stall the sampler
  delay(10000);
  Serial.println("\nRoadRec starting");
  Serial.printf("[mem] heap free %u B, PSRAM %u B free of %u B\n", (unsigned)ESP.getFreeHeap(),
                (unsigned)ESP.getFreePsram(), (unsigned)ESP.getPsramSize());
#if REPLAY_TEST
  Serial.println("[mode] REPLAY_TEST: playing /replay/*.csv through the detector (IMU not used)");
#endif
#if GPS_LOOPBACK_TEST
  Serial.println("[mode] GPS_LOOPBACK_TEST: jumper D6 <-> D7 and watch the [gps-loop] lines");
#endif

  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);
  if (imu.begin(Wire)) {
    imuOk = true;
    Serial.println("IMU ok");
  } else {
    Serial.println("IMU not found (check SDA/SCL wiring, 3V3, address 0x68).");
    i2cDiag();
    if (!SD_TEST_NO_IMU) {
      for (;;) { delay(2000); Serial.println("IMU not found. Halting."); }
    }
    Serial.println("SD_TEST_NO_IMU is 1: continuing without an IMU (readings are fake, for the SD test only)");
  }

  gps.begin(Serial1, PIN_GPS_RX, PIN_GPS_TX, 9600);
  Serial.println("GPS serial started (needs a clear sky view for a fix)");

  sdOk = store.begin(PIN_SD_SCK, PIN_SD_MISO, PIN_SD_MOSI, PIN_SD_CS);
  if (sdOk) {
    Serial.printf("SD ok, next event number %lu\n", (unsigned long)store.nextNumber());
    Serial.printf("SD card=%llu MB, mounted filesystem=%llu MB\n",
                  (unsigned long long)(SD.cardSize() / 1048576ULL),
                  (unsigned long long)(SD.totalBytes() / 1048576ULL));
  } else {
    Serial.println("SD missing - running WITHOUT saving");
  }
  if (sdOk && RAW_LOG && !REPLAY_TEST) {
    rawBuf = xMessageBufferCreate(40000);      // about 10 s of samples while the card is busy
    rawOn = rawBuf != nullptr && rawlog.begin();
    Serial.println(rawOn ? "raw log on" : "raw log FAILED to start");
  }

#ifdef POWER_FAIL_PIN
  pinMode(POWER_FAIL_PIN, INPUT_PULLUP);
#endif

  det = new rr::Detector();
  for (int k = 0; k < NSLOTS; k++) slots[k].win.reserve(rr::WIN_N);
  writeQ = xQueueCreate(NSLOTS, sizeof(uint8_t));
  xTaskCreatePinnedToCore(writerTask, "writer", 12288, nullptr, 1, nullptr, 0);
#if REPLAY_TEST
  xTaskCreatePinnedToCore(replayTask, "replay", 10000, nullptr, 3, nullptr, 1);
#else
  xTaskCreatePinnedToCore(samplerTask, "sampler", 8192, nullptr, 3, nullptr, 1);
#endif
  Serial.println("recording");
}

void loop() { vTaskDelay(portMAX_DELAY); }
