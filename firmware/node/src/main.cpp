// Sensor node: sample the ADXL345 and INMP441, stream both as frames over UART0, and run the
// on-device detectors on the same samples.
#include <Arduino.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>

#include <atomic>

#include "adxl345.h"
#include "frame.h"
#include "inmp441.h"
#include "ml_task.h"
#include "pins.h"
#include "uplink.h"

namespace {

constexpr uint32_t kBaud = 921600;
constexpr uint8_t kAccelDrainAt = 16;  // half the FIFO, so a late poll still has 5 ms of slack
constexpr uint32_t kHealthPeriodMs = 10000;
constexpr uint32_t kDebounceMs = 50;

// VSPI for the accelerometer leaves HSPI free for an optional LoRa module, which then cannot stall its reads.
SPIClass accelSpi(VSPI);
Adxl345 accel(accelSpi, PIN_ADXL_CS);
Inmp441 mic;

std::atomic<uint32_t> accelOverruns{0};
std::atomic<uint32_t> audioOverruns{0};

void accelTask(void*) {
  int16_t samples[frame::kAccelSamples * 3];
  size_t n = 0;
  int64_t frameStartUs = 0;
  uint32_t seq = 0;
  for (;;) {
    const uint8_t entries = accel.fifoEntries();
    if (entries < kAccelDrainAt) {
      vTaskDelay(1);
      continue;
    }
    if (entries >= Adxl345::kFifoDepth) accelOverruns++;
    const int64_t now = esp_timer_get_time();
    for (uint8_t k = 0; k < entries; k++) {
      AccelSample s;
      accel.readSample(s);
      // Entry k of n was sampled (n - 1 - k) periods of 312.5 us before the drain.
      if (n == 0) frameStartUs = now - static_cast<int64_t>(entries - 1 - k) * 625 / 2;
      samples[3 * n] = s.x;
      samples[3 * n + 1] = s.y;
      samples[3 * n + 2] = s.z;
      if (++n == frame::kAccelSamples) {
        send(frame::Stream::Accel, seq++, frameStartUs, samples, sizeof samples);
        mlFeed(ml::Kind::Accel, samples, frame::kAccelSamples);
        n = 0;
      }
    }
  }
}

void audioTask(void*) {
  int16_t samples[Inmp441::kBlock];
  uint32_t seq = 0;
  for (;;) {
    mic.read(samples);
    // The block completed just now, so its first sample is one block (62.5 us each) older.
    const int64_t startUs = esp_timer_get_time() - static_cast<int64_t>(Inmp441::kBlock) * 125 / 2;
    send(frame::Stream::Audio, seq++, startUs, samples, sizeof samples);
    mlFeed(ml::Kind::Audio, samples, Inmp441::kBlock);
    audioOverruns += mic.takeOverruns();
  }
}

void startAccel() {
  const uint8_t devid = accel.begin(PIN_ADXL_SCK, PIN_ADXL_MISO, PIN_ADXL_MOSI);
  if (devid != Adxl345::kDevId) {
    logf("adxl345 devid 0x%02X, expected 0x%02X, accel disabled", devid, Adxl345::kDevId);
    return;
  }
  xTaskCreatePinnedToCore(accelTask, "accel", 6144, nullptr, 5, nullptr, 1);
  logf("adxl345 ok, 3200 Hz, +/-16 g full resolution");
}

void startAudio() {
  const esp_err_t err = mic.begin(PIN_MIC_SCK, PIN_MIC_WS, PIN_MIC_SD);
  if (err != ESP_OK) {
    logf("i2s setup failed (%s), audio disabled", esp_err_to_name(err));
    return;
  }
  xTaskCreatePinnedToCore(audioTask, "audio", 6144, nullptr, 4, nullptr, 1);
  logf("inmp441 ok, 16 kHz");
}

// Commands arrive as newline-terminated text on the same UART the frames leave on.
void readCommands() {
  static char line[48];
  static size_t len = 0;
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (len > 0) {
        line[len] = '\0';
        mlCommand(line);
        len = 0;
      }
    } else if (len < sizeof line - 1) {
      line[len++] = c;
    }
  }
}

void readButton(uint32_t nowMs) {
  static bool wasDown = false;
  static uint32_t changedMs = 0;
  const bool down = digitalRead(PIN_BUTTON) == LOW;
  if (down == wasDown || nowMs - changedMs < kDebounceMs) return;
  wasDown = down;
  changedMs = nowMs;
  if (down) mlCommand("learn");
}

void showStatus(uint32_t nowMs) {
  const bool blink = (nowMs / 250) % 2 == 0;
  digitalWrite(PIN_LED, mlAlert() || (mlLearning() && blink) ? HIGH : LOW);
}

void logHealth() {
  char ml[96];
  mlHealth(ml, sizeof ml);
  logf("health: accel fifo overruns %u, audio dma overruns %u, tx drops %u, heap %u free "
       "(%u largest); %s",
       static_cast<unsigned>(accelOverruns.load()), static_cast<unsigned>(audioOverruns.load()),
       static_cast<unsigned>(uplinkDrops()), static_cast<unsigned>(ESP.getFreeHeap()),
       static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_8BIT)), ml);
}

}  // namespace

void setup() {
  uplinkBegin(kBaud);
  logf("mhm node, frame format v%u", frame::kVersion);
  pinMode(PIN_LED, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  if (mlBegin()) {
    logf("ml ready: send \"learn [seconds] [calibrate seconds]\" or press BOOT to learn normal");
  } else {
    logf("ml: not enough memory, detection disabled");
  }
  startAccel();
  startAudio();
}

void loop() {
  static uint32_t lastHealthMs = 0;
  const uint32_t nowMs = millis();
  readCommands();
  readButton(nowMs);
  showStatus(nowMs);
  if (nowMs - lastHealthMs >= kHealthPeriodMs) {
    lastHealthMs = nowMs;
    logHealth();
  }
  vTaskDelay(pdMS_TO_TICKS(20));
}
