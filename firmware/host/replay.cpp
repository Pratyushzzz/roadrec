// host/replay.cpp - run the firmware's detector on a recorded CSV, on a laptop.
// Input : 100 Hz CSV  t,ax,ay,az,gx,gy,gz,speed   (made by tools/export_resampled.py)
// Output: one line per event:  <trigger_time_s> <LABEL> <reason>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <cmath>
#include "roadcore.h"

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "usage: replay file.csv\n"); return 1; }
  FILE* fp = fopen(argv[1], "r");
  if (!fp) { perror("open"); return 1; }
  char line[512];
  fgets(line, sizeof line, fp);  // header
  rr::Detector det;
  int events = 0;
  while (fgets(line, sizeof line, fp)) {
    double t, v[7];
    char* p = line;
    char* tok;
    int k = 0;
    // parse comma-separated fields, treating empty / "nan" as NaN
    std::string s(line);
    size_t pos = 0;
    std::string fields[8];
    for (int i = 0; i < 8; i++) {
      size_t e = s.find(',', pos);
      fields[i] = s.substr(pos, e == std::string::npos ? std::string::npos : e - pos);
      pos = (e == std::string::npos) ? s.size() : e + 1;
    }
    (void)p; (void)tok; (void)k;
    t = atof(fields[0].c_str());
    for (int i = 0; i < 7; i++) {
      const std::string& f = fields[i + 1];
      v[i] = (f.empty() || f[0] == 'n' || f[0] == 'N' || f[0] == '\n' || f[0] == '\r') ? NAN : atof(f.c_str());
    }
    rr::Sample smp{{(float)v[0], (float)v[1], (float)v[2]}, {(float)v[3], (float)v[4], (float)v[5]}, (float)v[6]};
    (void)t;
    if (det.push(smp)) {
      rr::Result r = det.finishEvent();
      printf("%.2f %s %s\n", det.triggerSampleCount() / 100.0, rr::labelName(r.label), r.reason);
      events++;
    }
  }
  fclose(fp);
  while (det.flush()) {  // recording ended mid-event: classify what exists (same as a power-loss save); loop for queued events
    rr::Result r = det.finishEvent();
    printf("%.2f %s %s\n", det.triggerSampleCount() / 100.0, rr::labelName(r.label), r.reason);
    events++;
  }
  fprintf(stderr, "%d events\n", events);
  return 0;
}
