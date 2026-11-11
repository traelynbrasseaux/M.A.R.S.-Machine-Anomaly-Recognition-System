# Node to host interface

This is the contract between the node firmware (`firmware/node/`) and the host tools (`host/mhm/capture/`). If you change an existing field or stream here, bump `VERSION` in both `firmware/node/include/frame.h` and `host/mhm/capture/frame.py`. A new stream type is additive and keeps the version, because older parsers skip frames they don't know.

## Link

- UART0 through the dev board's CP2102 USB-UART chip, 921600 baud, 8N1, no flow control. 921600 is at the top of the CP2102's range, so don't plan on raising the baud rate later. Windows and macOS may need the Silicon Labs CP210x driver.
- The node streams binary frames continuously from boot. The host can send text commands the other way (see [Commands](#commands)).
- The ROM bootloader prints text at 115200 baud on reset. The host parser skips anything that is not a valid frame, so that text is harmless.

## Frame format (version 1)

All multi-byte fields are little-endian (the ESP32's native order).

| Offset | Size | Field          | Notes |
|-------:|-----:|----------------|-------|
| 0      | 2    | magic          | `0xA5 0x5A` |
| 2      | 1    | version        | `1` |
| 3      | 1    | stream         | `1` accel, `2` audio, `3` log, `4` score |
| 4      | 4    | seq            | uint32, per stream, starts at 0 on boot, wraps at 2^32 |
| 8      | 8    | timestamp_us   | uint64, node time of the first sample in the frame (see Timing) |
| 16     | 2    | sample_count   | uint16, samples in the payload (see per-stream sizes) |
| 18     | N    | payload        | stream-specific |
| 18 + N | 2    | crc            | CRC-16 over bytes 0 to 17 + N (magic through payload) |

Header plus trailer is 20 bytes per frame.

**CRC:** CRC-16/CCITT-FALSE: polynomial `0x1021`, init `0xFFFF`, no input or output reflection, no final XOR. Check value: the ASCII bytes `123456789` give `0x29B1`.

**Sequence numbers** count frames produced, not frames sent. If the node has to drop a frame because its transmit queue is full, the host still sees the gap. A gap of `k` means `k` frames are missing, either dropped on the node or rejected by the host for a bad CRC. A sequence number that goes backwards means the node rebooted.

**Parser rules:** scan for the magic, reject the candidate if `version` or `stream` is unknown or `sample_count` exceeds 1024, then wait for the full frame and check the CRC. On any rejection, advance one byte and scan again. Frames are never assumed to be back to back.

## Streams

### Accel (stream 1)

- ADXL345 over SPI, 3200 Hz output data rate (1600 Hz bandwidth), FIFO in stream mode.
- `DATA_FORMAT`: full resolution, +/-16 g range, so the scale is 3.9 mg/LSB at every range setting.
- One sample is three int16 values in the order x, y, z. Payload is `x0 y0 z0 x1 y1 z1 ...`, so `N = 6 * sample_count`.
- The node sends 32 samples per frame (10 ms): 192 B payload, 212 B frame, 100 frames/s.

### Audio (stream 2)

- INMP441 over I2S at 16 kHz, mono (L/R pin tied to GND, so the mic drives the left slot).
- The INMP441 produces 24-bit two's complement samples, MSB first, in a 32-bit slot. The ESP32 I2S peripheral reads each slot as an int32 with the 24 data bits in bits 31 to 8 and zeros below.
- **Downshift:** the node sends `(int16_t)(raw32 >> 16)`, an arithmetic shift that keeps the top 16 of the 24 bits. Full scale (0 dBFS) is 120 dB SPL (sensitivity is -26 dBFS at 94 dB SPL), so one 16-bit LSB is -96 dBFS, about 24 dB SPL. That is below the mic's own noise floor of about 33 dBA SPL (61 dBA SNR), so the 8 discarded bits carry only mic self-noise. The shift is fixed in version 1. If machines turn out too quiet, a smaller shift with saturation is a version 2 change.
- Payload is `s0 s1 s2 ...` as int16, so `N = 2 * sample_count`.
- The node sends 256 samples per frame (16 ms): 512 B payload, 532 B frame, 62.5 frames/s.

### Log (stream 3)

- UTF-8 text with no terminator, `N = sample_count` bytes, at most 1024.
- Used for boot messages, sensor bring-up errors, and periodic node health counters. The UART carries only binary frames, so this is the only way the node can report text without corrupting the stream.
- The health line every 10 s includes free heap and the largest free block, plus the worst time the detector took to process one frame of samples on each stream.

### Score (stream 4)

One frame per stream per detector block (about 1 s) while the detector is not idle. `sample_count` is 1 and the 28-byte payload is:

| Offset | Size | Field | Notes |
|-------:|-----:|-------|-------|
| 0 | 1 | stream | `1` accel or `2` audio: which stream this block scored |
| 1 | 1 | phase | `0` idle, `1` ranges, `2` bundle, `3` calibrate, `4` detect |
| 2 | 1 | alerts | bit 0 Mahalanobis, bit 1 HDC binary, bit 2 HDC bipolar; only set in detect |
| 3 | 1 | pad | 0 |
| 4 | 12 | scores | three float32, the mean window score over the block, in the same model order; 0 while learning |
| 16 | 12 | thresholds | three float32; 0 until calibration ends |

The timestamp is when the node finished the block. Score frames add under 100 B/s.

## Commands

The host sends newline-terminated ASCII lines on the same UART. The node answers each one with a log frame. `mhm capture --send "..."` sends commands right after opening the port; any tool that can write to the serial port works too, including a pyserial URL such as `socket://` or `rfc2217://` for a remote bridge.

| Command | Effect |
|---------|--------|
| `learn [learn_s] [calibrate_s]` | Start over on both streams: ranges for half of `learn_s`, bundle for the other half, then calibrate for `calibrate_s`, then detect. Defaults are 60 and 30. Pressing the BOOT button is `learn` with the defaults. |
| `detect` | Resume detecting with the model already learned |
| `stop` | Stop learning or detecting; raw streaming continues |
| `margin <x>` | Threshold = highest calibration block score times `x` (default 1.2); applies right away |
| `alert <mahalanobis\|binary\|bipolar>` | Which model drives the LED (default `mahalanobis`) |
| `status` | Log each stream's phase, the margin, and the alert model |

The LED blinks while learning or calibrating and stays lit while the chosen model is alerting on either stream. Learning lives in RAM only, so a reboot needs a new `learn`.

## Bandwidth

At 921600 baud with 8N1 framing, each byte costs 10 bit times:

```
link capacity   = 921600 / 10                       = 92160 B/s

accel payload   = 3200 samples/s * 3 axes * 2 B     = 19200 B/s
accel overhead  = 100 frames/s * 20 B               =  2000 B/s
accel total                                         = 21200 B/s

audio payload   = 16000 samples/s * 2 B             = 32000 B/s
audio overhead  = 62.5 frames/s * 20 B              =  1250 B/s
audio total                                         = 33250 B/s

both streams                                        = 54450 B/s
utilization     = 54450 / 92160                     = 59.1 %
headroom                                            = 37710 B/s (40.9 %)
```

Log frames add well under 100 B/s. The next standard rate down, 460800 baud (46080 B/s), would not fit both streams, so 921600 is required.

## Timing

- `timestamp_us` comes from `esp_timer_get_time()`, in microseconds since node boot. It is the estimated acquisition time of the first sample in the frame.
- **Accel:** when the node drains the FIFO at time `t` and finds `n` entries, entry `k` (0 is oldest) is stamped `t - (n - 1 - k) * 312.5 us`. Jitter is a few tens of microseconds.
- **Audio:** when an I2S DMA buffer of 256 samples completes at time `t`, its first sample is stamped `t - 256 * 62.5 us`. Jitter is up to one FreeRTOS tick if the reader task is late.
- The host reconstructs per-sample times as `timestamp_us + i * 1e6 / fs`. A jump much larger than one frame without a sequence gap points to a sensor-side overrun (ADXL345 FIFO full or I2S DMA overflow), and the node also reports those counts in its log frames.

## Capture files

The host writes each capture to `data/captures/` (gitignored):

```
{machine}_{condition}_{speed}_{date}_{run}.bin
```

- `machine`, `condition`, `speed`: lowercase tokens of `a-z`, `0-9`, and `-` only, so `_` stays unambiguous as the separator. Vocabulary is in [capture-conventions.md](capture-conventions.md).
- `date`: local date at the start of the capture, `YYYYMMDD`.
- `run`: two digits, starting at `01` for each machine, condition, speed, and date combination.
- Example: `fan1_unbalance_1500rpm_20261012_02.bin`

The `.bin` file is the raw byte stream exactly as received, including any garbage between frames. The loader reparses it with the same parser, so a parser fix can be applied to old captures.

`data/captures/captures.csv` gets one row per capture, appended when the capture ends:

| Column           | Meaning |
|------------------|---------|
| `file`           | file name of the `.bin` |
| `machine`        | as in the file name |
| `condition`      | as in the file name |
| `speed`          | as in the file name |
| `date`           | `YYYYMMDD` |
| `run`            | integer |
| `start_utc`      | ISO 8601 start time |
| `duration_s`     | wall clock length of the capture |
| `bytes`          | size of the `.bin` |
| `accel_frames`   | valid accel frames |
| `audio_frames`   | valid audio frames |
| `crc_errors`     | frames rejected for a bad CRC |
| `dropped_accel`  | accel frames missing from sequence gaps |
| `dropped_audio`  | audio frames missing from sequence gaps |
| `port`           | serial port or `-` for stdin |
| `notes`          | free text from `--notes` |

## Pin map

The node is an ELEGOO ESP-WROOM-32 dev board (original ESP32, CP2102, USB-C). Pins are the GPIO numbers on the board's silkscreen, and `firmware/node/include/pins.h` must match this table.

| Signal | Module pin | ESP32 GPIO | Notes |
|--------|-----------|------------|-------|
| **ADXL345 (GY-291), on VSPI** | | | |
| SPI clock | SCL | 18 | SPI mode 3, firmware runs 5 MHz (the chip's maximum) |
| SPI data out (MISO) | SDO | 19 | |
| SPI data in (MOSI) | SDA | 23 | labelled for I2C, acts as data in for SPI |
| Chip select | CS | 5 | the module pulls it high, so boot is safe |
| FIFO interrupt | INT1 | 34 | wired but unused: the firmware polls `FIFO_STATUS` |
| Power | VCC / GND | 3V3 / GND | |
| **INMP441, on I2S** | | | |
| Bit clock | SCK | 26 | 1.024 MHz at 16 kHz |
| Word select | WS | 25 | |
| Data | SD | 33 | |
| Channel select | L/R | GND | selects the left slot |
| Power | VDD / GND | 3V3 / GND | |
| **Onboard, not used yet** | | | |
| Status LED | | 2 | |
| BOOT button | | 0 | usable as an input after startup |
| **Kept free for an optional LoRa module on HSPI (stretch goal; alerts use the built-in Wi-Fi)** | | | |
| SPI clock / MISO / MOSI / CS | | 14 / 27 / 13 / 4 | moved off GPIO 12, which can stop the board booting |
| Reset / IRQ / Busy | | 32 / 35 / 39 | Busy exists only on the SX1262 |
| **Gateway board only** | | | |
| OLED SDA / SCL | | 21 / 22 | SSD1306 display |

The map stays off the flash pins (6 to 11) and the boot strapping pins 12 and 15, and uses the input-only pins 34, 35 and 39 only for inputs. The ADXL345 needs SPI rather than I2C to keep up at 3200 Hz, and the accelerometer gets its own bus so a later LoRa module never stalls its reads.

Wiring for clean data: screw or epoxy the ADXL345 module to a rigid plate on the magnet and run short wires (under about 20 cm) to a breadboard kept off the machine. A sensor sitting on a breadboard measures the breadboard, and long loose jumpers cause SPI errors at 5 MHz.
