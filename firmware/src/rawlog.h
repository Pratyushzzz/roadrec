#pragma once
#include <Arduino.h>
#include <SD.h>
#include "roadcore.h"

class RawLog {
 public:
  bool begin() {                                   // call after SD.begin() succeeded
    ok_ = false;
    if (!SD.exists("/raw") && !SD.mkdir("/raw")) { Serial.println("raw log: FAILED mkdir /raw"); return false; }
    unsigned next = 1;
    File root = SD.open("/raw");
    if (root) {
      for (File e = root.openNextFile(); e; e = root.openNextFile()) {
        const char* nm = e.name();
        const char* slash = strrchr(nm, '/');
        if (slash) nm = slash + 1;
        unsigned n = 0;
        if (sscanf(nm, "raw_%u", &n) == 1 && n >= next) next = n + 1;
        e.close();
      }
      root.close();
    }
    char path[40];
    snprintf(path, sizeof path, "/raw/raw_%04u.csv", next);
    f_ = SD.open(path, FILE_WRITE);
    if (!f_) { Serial.printf("raw log: FAILED to open %s\n", path); return false; }
    strlcpy(path_, path, sizeof path_);
    f_.print("t,ax,ay,az,gx,gy,gz,speed\n");
    f_.flush();                                    // file shows up on the card immediately
    ok_ = true;
    Serial.printf("raw log: %s\n", path_);
    return true;
  }

  void add(const rr::Sample& s) {
    if (!ok_) return;                              // not open: don't pretend to log
    if (len_ > sizeof buf_ - 128) writeOut();
    len_ += snprintf(buf_ + len_, sizeof buf_ - len_, "%.2f,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,",
                     count_ / (double)rr::FS, s.a[0], s.a[1], s.a[2], s.w[0], s.w[1], s.w[2]);
    if (!isnan(s.speed)) len_ += snprintf(buf_ + len_, sizeof buf_ - len_, "%.2f", s.speed);
    buf_[len_++] = '\n';
    count_++;
    if (count_ % rr::FS == 0) { writeOut(); f_.flush(); }
  }

  bool ok() const { return ok_; }
  const char* path() const { return ok_ ? path_ : "-"; }
  uint32_t lines() const { return count_; }
  uint32_t bytes() const { return bytes_; }
  uint32_t write_errors() const { return werr_; }

 private:
  void writeOut() {
    if (len_) {
      size_t n = f_ ? f_.write((const uint8_t*)buf_, len_) : 0;
      bytes_ += n;
      if (n != len_) werr_++;
    }
    len_ = 0;
  }
  File f_;
  char buf_[2048], path_[40] = "";
  size_t len_ = 0;
  uint32_t count_ = 0, bytes_ = 0, werr_ = 0;
  bool ok_ = false;
};