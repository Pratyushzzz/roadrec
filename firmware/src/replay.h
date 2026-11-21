// Bench test helper: play recorded CSVs from the SD card through the detector, as if they
// were live IMU data. Needs no IMU and no GPS.
//   SD card: put 100 Hz CSVs (columns t,ax,ay,az,gx,gy,gz,speed) into /replay/
//   (make them on the Mac with tools/export_resampled.py).

#pragma once

#include <Arduino.h>
#include <SD.h>
#include <string.h>
#include <string>
#include <vector>
#include <algorithm>
#include "roadcore.h"

class Replay {
 public:

  // CSV files in `dir`, sorted by name.
  // Skips hidden files (macOS writes "._name" junk onto cards).
  std::vector<std::string> list(const char* dir) {
  std::vector<std::string> v;

  // First inspect the SD card root.
  File root = SD.open("/");

  if (!root) {
    Serial.println("[replay-debug] FAILED to open SD root");
    return v;
  }

  Serial.println("[replay-debug] SD ROOT:");

  for (File e = root.openNextFile(); e; e = root.openNextFile()) {
    Serial.printf("[replay-debug] ROOT ENTRY: %s | dir=%d | size=%lu\n",
                  e.name(),
                  e.isDirectory(),
                  (unsigned long)e.size());
    e.close();
  }

  root.close();

  // Now inspect /replay.
  root = SD.open(dir);

  if (!root) {
    Serial.printf("[replay-debug] FAILED to open: %s\n", dir);
    return v;
  }

  Serial.printf("[replay-debug] opened: %s, isDir=%d\n",
                dir, root.isDirectory());

  for (File e = root.openNextFile(); e; e = root.openNextFile()) {

    Serial.printf("[replay-debug] found: %s | dir=%d | size=%lu\n",
                  e.name(),
                  e.isDirectory(),
                  (unsigned long)e.size());

    std::string nm = e.name();

    size_t sl = nm.rfind('/');
    if (sl != std::string::npos)
      nm = nm.substr(sl + 1);

    bool isDir = e.isDirectory();

    e.close();

    if (isDir || nm.empty() || nm[0] == '.')
      continue;

    if (nm.size() < 5 ||
        strcasecmp(nm.c_str() + nm.size() - 4, ".csv") != 0)
      continue;

    v.push_back(nm);
  }

  root.close();

  std::sort(v.begin(), v.end());

  return v;
}
  // Reads a whole CSV into PSRAM.
  // Caller frees *out with free().
  bool load(const std::string& path, rr::Sample** out, size_t* n) {

    File f = SD.open(path.c_str(), FILE_READ);

    if (!f)
      return false;

    size_t cap = f.size() / 40 + 16;
    // A row is never shorter than ~40 bytes.

    rr::Sample* buf =
        (rr::Sample*)ps_malloc(cap * sizeof(rr::Sample));

    if (!buf)
      buf = (rr::Sample*)malloc(cap * sizeof(rr::Sample));

    if (!buf) {
      f.close();
      return false;
    }

    size_t k = 0;

    char line[160];

    bool header = true;

    while (f.available() && k < cap) {

      size_t L =
          f.readBytesUntil('\n', line, sizeof line - 1);

      line[L] = 0;

      // Skip CSV header
      if (header) {
        header = false;
        continue;
      }

      if (L < 10)
        continue;

      if (parseLine(line, buf[k]))
        k++;
    }

    f.close();

    *out = buf;
    *n = k;

    return true;
  }


  void appendResult(const std::string& name,
                    const std::string& labels) {

    File f = SD.open("/replay_results.txt", FILE_APPEND);

    if (!f)
      return;

    f.printf("%s: %s\n",
             name.c_str(),
             labels.c_str());

    f.close();
  }


 private:

  // CSV format:
  // t,ax,ay,az,gx,gy,gz,speed
  //
  // speed may be empty = unknown

  static bool parseLine(char* p, rr::Sample& s) {

    char* c = strchr(p, ',');

    if (!c)
      return false;

    char* cur = c + 1;

    float v[7];

    int k = 0;

    for (; k < 7; k++) {

      char* e = strchr(cur, ',');

      if (e)
        *e = 0;

      size_t L = strlen(cur);

      while (L &&
             (cur[L - 1] == '\r' ||
              cur[L - 1] == '\n')) {

        cur[--L] = 0;
      }

      v[k] =
          (L == 0 ||
           cur[0] == 'n' ||
           cur[0] == 'N')
              ? NAN
              : strtof(cur, nullptr);

      if (!e)
        break;

      cur = e + 1;
    }

    for (int j = k + 1; j < 7; j++)
      v[j] = NAN;

    // Accelerometer and gyroscope values must be valid
    for (int j = 0; j < 6; j++) {

      if (std::isnan(v[j]))
        return false;
    }

    for (int j = 0; j < 3; j++) {

      s.a[j] = v[j];
      s.w[j] = v[3 + j];
    }

    s.speed = v[6];

    return true;
  }
};