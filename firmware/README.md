# RoadRec firmware (XIAO ESP32-S3)

Records IMU + GPS at 100 Hz into a rolling buffer. When something happens it keeps
10 s before and 6 s after, classifies it with the same physics rules as the Python reference classifier (`classifier.py`, see the top-level README),
and saves the window to the SD card.

## Status: what is and isn't verified

| Part | Status |
|---|---|
| `lib/roadcore` (detector + classifier, C++) | **Tested on a laptop.** Gives the same event labels as `classifier.py` on 70/70 synthetic recordings and on all 30 real phone recordings (`tools/compare.py`). |
| `src/imu.h`, `gps.h`, `storage.h`, `main.cpp` | **Syntax-checked only** (against stub headers; not built with the ESP32 toolchain). Never run on a board. Expect to debug wiring, I2C/SD details and timing on first power-up. |
| Power-loss survival, vehicle power, case | Not built. `POWER_FAIL_PIN` hook exists but needs the hardware. |

Behaviour notes:
- While an event is being captured (6 s after its trigger), a separate trigger more than 2 s after the previous one is queued (up to 4) and captured as its own event right afterwards. This matches Python, which reports every separate event, and it means a crash shortly after a bump is not swallowed by the bump's window.
- Finished windows go to a 3-slot write queue, so two events ~2 s apart are both saved even if the SD card stalls briefly. If all three slots are busy the event is dropped and `dropped=` in the status line counts it.
- A recording that ends mid-event is classified with `flush()` (this is also what the power-fail pin does). `flush()` returns one window per call; the host replay calls it in a loop to drain the queue. The power-fail path saves only the capture in progress.
- The classifier does not depend on how the board is mounted: it measures gravity itself and uses only angles and magnitudes, so any fixed orientation works.

## Wiring (3.3 V logic)

| Part | Pin on the part | XIAO pin |
|---|---|---|
| MPU6050 | SDA | D4 (GPIO5) |
| MPU6050 | SCL | D5 (GPIO6) |
| MPU6050 | VCC / GND | 3V3 / GND |
| GPS | TX | D7 (GPIO44) |
| GPS | RX | D6 (GPIO43) |
| GPS | VCC / GND | per module (3V3 or 5V) / GND |
| SD card | the slot on the Sense expansion board | uses GPIO21 (CS), 7, 8, 9 |

Do not use GPIO21 for an LED: on this board it is the SD chip select. Check these pins against
the XIAO ESP32-S3 pinout sheet before soldering.

## Build and flash (PlatformIO + pioarduino)

1. In VS Code with the PlatformIO extension: File > Open Folder > this `firmware/` folder.
2. Click the PlatformIO check mark (Build), then the arrow (Upload) with the board plugged in over a USB-C **data** cable.
3. Open the serial monitor at 115200. You should see `IMU ok`, `SD ok`, `recording`, and a `[status]` line every 10 s.
   - `gps_sentences` greater than 0 means the GPS is talking to the board. `gps_fix=1` needs a view of the sky.
   - `-DARDUINO_USB_CDC_ON_BOOT=1` is already in `platformio.ini` (the XIAO has no USB-serial chip, and `Serial` would otherwise use the GPS pins). If the monitor is empty, reset the board after opening it.

## What it writes

`/events/evt_0001_DROP.csv`, a `evt_0001_DROP.txt` summary, and `evt_0001_DROP.sha256` (SHA-256 fingerprint of the CSV, used by the web reader's integrity check). A file ending `.part` was cut off mid-write.
The CSV has the exact columns `classifier.py` reads, so the same report pipeline works on device data:

    python3 classifier.py evt_0001_DROP.csv --out out_evt1

(Accelerations are in g and rotation in deg/s, so no `--units` flag is needed.)

## Test the logic without hardware

    # from firmware/
    g++ -O2 -std=c++17 -Ilib/roadcore host/replay.cpp lib/roadcore/roadcore.cpp -o host/replay
    python3 tools/compare.py path/to/folder_of_sensor_logger_zips

`compare.py` needs `classifier.py` and `convert.py` (the Python reference implementation). Put them in `python/` at the repo root, or set `ROADREC_PY=/path/to/folder`.

`compare.py` runs both the Python classifier and the C++ detector on every recording and
prints whether the event labels match.

## Things to check first on real hardware

1. IMU: put the board flat and still. The `[status]` line prints `|a|`; it should read about 1.0 g (0.97 to 1.03 is normal). `clip=` counts samples at the 16 g limit.
2. Sampling: `late` in the status line should stay at 0, and `imu_err` at 0.
3. Compare a bench drop against the phone drops. The MPU6050 differs from a phone IMU, so thresholds may need tuning.
4. MPU6050 clips at 16 g. A hard crash will be clipped, so the peak is a lower bound.
5. Cut power mid-write and confirm that no half-written `.csv` appears.

## Next steps

- A 12 V to 5 V supply with surge protection, and a LiPo or supercapacitor reserve driving `POWER_FAIL_PIN`.
- A higher sample rate with peak-hold, so very short spikes are never missed between reads.
- Replace the MPU6050 with an ICM-42688, or add a high-g accelerometer (ADXL375) if clipping matters.
