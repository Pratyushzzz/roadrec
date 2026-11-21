# Bench tests for the XIAO ESP32-S3 Sense (no IMU, no GPS needed)

These tests need only the XIAO ESP32-S3 Sense board and its SD card. Run all commands from `firmware/`.
The test modes are `#define` switches at the top of `src/main.cpp`.

Build and upload with `pio run -t upload`, then watch with `pio device monitor`.
Test modes are switches at the top of `main.cpp`. Change one, upload, test.

## Test 1: memory and boot (no change needed)
Boot messages now include a `[mem]` line. PSRAM should show about 8 MB. Note the numbers.

## Test 2: SD raw log (current setting, REPLAY_TEST 0)
Let it run 2 minutes, unplug, take the card to the Mac and open `/raw/raw_000N.csv`.
Pass: first column rises by 0.01 per row, no gaps, about 100 rows per second, no half-written last row.
(The values are the fake flat 1 g, so only the format and timing matter.)

## Test 3: replay real recordings through the board (the main one)
Runs your real phone recordings through the firmware's detector on the actual chip, then saves
each event the way a real event would be saved.

1. On the Mac, from `firmware/`, make 100 Hz CSVs from some recordings (zsh):
   ```
   mkdir -p ~/Desktop/replay
   for f in ../python/recordings/Drop_5-*.zip ../python/recordings/Bump_4-*.zip ../python/recordings/Tip_over_1-*.zip ../python/recordings/N_2-*.zip; do
     python3 tools/export_resampled.py "$f" ~/Desktop/replay/$(basename "$f" .zip | cut -d- -f1).csv
   done
   ```
   (Adjust the folder and names to your recordings. `N_2` is 141 s and full of events, so it is a good stress test.)
2. Copy the `replay` folder to the **root** of the SD card (so the files are at `/replay/Drop_5.csv` etc.). The card must be FAT32.
3. In `main.cpp` set `#define REPLAY_TEST 1`, upload, open the monitor.
4. Watch for `[replay-result] name: LABELS` per file, then `[replay] DONE ...`.
   Pass: `late=0`, `dropped=0`, and the labels match the laptop.
5. Copy `/replay_results.txt` from the card to `~/Desktop`, then:
   ```
   g++ -O2 -std=c++17 -Ilib/roadcore host/replay.cpp lib/roadcore/roadcore.cpp -o host/replay
   python3 tools/device_vs_host.py ~/Desktop/replay_results.txt ~/Desktop/replay
   ```
   Pass: every file says `match`. A `DIFF` means the chip decided differently from the laptop: open an issue with the file.
6. The saved event files are in `/events/`. Copy one to the Mac and run
   `python3 ../python/classifier.py evt_0001_DROP.csv --out out_evt1` to get the report plot from device data.
7. Speed up long replays with `#define REPLAY_SPEEDUP 5` (five samples per tick). Real time (1) is the honest timing test.

Also note `[sd] event N saved in X ms`: that is how long a save takes, which sizes the power reserve later.

## Test 4: GPS serial pins and parser (needs one jumper wire)
1. Connect D6 to D7 with one jumper wire (the board's TX to its own RX).
2. Set `#define GPS_LOOPBACK_TEST 1`, upload, open the monitor.
3. Every second you should see `[gps-loop] sent X m/s got Y m/s OK`, stepping through no-fix (-1.0), 5, 10, 15, 20 m/s every 5 s.
   Pass: `got` follows `sent`, and the `sentences ok` counter keeps rising. One MISMATCH right at a step change is normal.
   Fail with the jumper in place: the pins or parser are wrong. Fail without a jumper is expected.
4. Remove the jumper afterwards, since a real GPS module uses those pins.

## Test 5: reboot numbering
After Test 3, press reset. New events must continue after the highest `evt_` number on the card, not start again at 1.

## Test 6: power cut during SD writes
Run Test 2 (raw log), then pull the USB cable while it is running. On the Mac the CSV should open fine and lose at most the last second or so.
For event files: a `.part` file means a save was cut off, and a `.csv` file must always be complete.
(Timing a cut exactly inside an event save is hard because saves are fast; you will test that properly with a power reserve.)

## Set everything back
Set `REPLAY_TEST 0` and `GPS_LOOPBACK_TEST 0` before using the real IMU. Also set `SD_TEST_NO_IMU 0` once the IMU is wired.

## What these tests cannot tell you
Real sensor behaviour (noise, vibration, clipping at 16 g, axis directions) and real GPS fixes. That needs the MPU6050 and a GPS module.
