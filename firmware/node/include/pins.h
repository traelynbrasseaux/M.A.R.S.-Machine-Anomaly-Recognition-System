// Node wiring by the GPIO numbers on the dev board's silkscreen; mirrors docs/interface.md.
#pragma once

// ADXL345 (GY-291 module) on VSPI, SPI mode 3. The module pulls CS high, so GPIO 5 boots safely.
constexpr int PIN_ADXL_SCK = 18;
constexpr int PIN_ADXL_MISO = 19;  // module pin SDO
constexpr int PIN_ADXL_MOSI = 23;  // module pin SDA
constexpr int PIN_ADXL_CS = 5;
constexpr int PIN_ADXL_INT1 = 34;  // wired but unused: the firmware polls FIFO_STATUS

// INMP441 on I2S, with its L/R pin tied to GND for the left slot.
constexpr int PIN_MIC_SCK = 26;
constexpr int PIN_MIC_WS = 25;
constexpr int PIN_MIC_SD = 33;

// Onboard parts, not used by the firmware yet.
constexpr int PIN_LED = 2;
constexpr int PIN_BUTTON = 0;  // BOOT button, usable as an input after startup

// Kept free for an optional LoRa module on HSPI (a stretch goal; alerts use Wi-Fi), skipping GPIO 12 (high at boot blocks startup).
constexpr int PIN_LORA_SCK = 14;
constexpr int PIN_LORA_MISO = 27;
constexpr int PIN_LORA_MOSI = 13;
constexpr int PIN_LORA_CS = 4;
constexpr int PIN_LORA_RST = 32;
constexpr int PIN_LORA_IRQ = 35;
constexpr int PIN_LORA_BUSY = 39;  // SX1262 only
