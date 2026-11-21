// SD card event storage. Each event becomes a CSV in /events that classifier.py can read
// directly (columns t,ax,ay,az,gx,gy,gz,speed), plus a small .txt summary.
// Files are written as *.part and renamed only when complete, so a power cut mid-write
// leaves an obviously incomplete file instead of a silently truncated "good" one.
#pragma once
#include <Arduino.h>
#include <SD.h>
#include <SPI.h>
#include "roadcore.h"
#include "mbedtls/sha256.h"

class Storage {
 public:
  bool begin(int sck, int miso, int mosi, int cs) {
    SPI.begin(sck, miso, mosi, cs);
    if (!SD.begin(cs, SPI, 20000000)) return false;
    if (!SD.exists("/events")) SD.mkdir("/events");
    // continue numbering after whatever is already on the card
    File root = SD.open("/events");
    if (root) {
      for (File e = root.openNextFile(); e; e = root.openNextFile()) {
        const char* nm = e.name();
        const char* slash = strrchr(nm, '/');
        if (slash) nm = slash + 1;
        unsigned n = 0;
        if (sscanf(nm, "evt_%u", &n) == 1 && n >= next_) next_ = n + 1;
        e.close();
      }
      root.close();
    }
    return true;
  }

  uint32_t nextNumber() const { return next_; }

  bool save(const rr::Sample* win, int n, const rr::Result& r, uint32_t trigSample, uint32_t* noOut = nullptr) {
    uint32_t no = next_++;
    if (noOut) *noOut = no;
    char base[64], part[72], fin[72];
    snprintf(base, sizeof base, "/events/evt_%04u_%s", (unsigned)no, rr::labelName(r.label));
    snprintf(part, sizeof part, "%s.part", base);
    snprintf(fin, sizeof fin, "%s.csv", base);

    File f = SD.open(part, FILE_WRITE);
    if (!f) return false;
    static char buf[1024];
    size_t len = snprintf(buf, sizeof buf, "t,ax,ay,az,gx,gy,gz,speed\n");
    for (int i = 0; i < n; i++) {
      const rr::Sample& s = win[i];
      len += snprintf(buf + len, sizeof buf - len, "%.2f,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,", i / (float)rr::FS,
                      s.a[0], s.a[1], s.a[2], s.w[0], s.w[1], s.w[2]);
      if (!isnan(s.speed)) len += snprintf(buf + len, sizeof buf - len, "%.2f", s.speed);
      len += snprintf(buf + len, sizeof buf - len, "\n");
      if (len > sizeof buf - 160) {                     // write in chunks
        if (f.write((const uint8_t*)buf, len) != len) { f.close(); return false; }
        len = 0;
      }
    }
    if (len && f.write((const uint8_t*)buf, len) != len) { f.close(); return false; }
    f.flush();
    f.close();
    if (!SD.rename(part, fin)) return false;
      {   // fingerprint of the saved file, stored beside it as evt_NNNN_LABEL.sha256
         File rf = SD.open(fin, FILE_READ);
         if (rf) {
           mbedtls_sha256_context c;
           mbedtls_sha256_init(&c);
           mbedtls_sha256_starts(&c, 0);
           uint8_t b[256];
           size_t k;
           while ((k = rf.read(b, sizeof b)) > 0) mbedtls_sha256_update(&c, b, k);
           rf.close();
           uint8_t h[32];
           mbedtls_sha256_finish(&c, h);
           mbedtls_sha256_free(&c);
           char hp[72];
           snprintf(hp, sizeof hp, "%s.sha256", base);
           File o = SD.open(hp, FILE_WRITE);
           if (o) {
             for (int i = 0; i < 32; i++) o.printf("%02x", h[i]);
             o.printf("  evt_%04u_%s.csv\n", (unsigned)no, rr::labelName(r.label));
             o.close();
           }
         }
       }

    snprintf(fin, sizeof fin, "%s.txt", base);
    File t = SD.open(fin, FILE_WRITE);
    if (t) {
      t.printf("event %u: %s\nreason: %s\n", (unsigned)no, rr::labelName(r.label), r.reason);
      t.printf("trigger at %.2f s after boot (window index %d of %d)\n", trigSample / (float)rr::FS, r.f.i0, n);
      t.printf("peak %.1f g over %.0f ms, rotation peak %.0f deg/s, orientation change %.0f deg\n", r.f.peak_g,
               r.f.spike_ms, r.f.gyro_peak, r.f.post_tilt_deg);
      t.close();
    }
    return true;
  }

 private:
  uint32_t next_ = 1;
};
