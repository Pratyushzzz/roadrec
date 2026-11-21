// Minimal NMEA reader: only $xxRMC (status + speed over ground). No library needed.
#pragma once
#include <Arduino.h>
#include <math.h>

class Gps {
 public:
  void begin(HardwareSerial& port, int rxPin, int txPin, uint32_t baud = 9600) {
    port_ = &port;
    port_->begin(baud, SERIAL_8N1, rxPin, txPin);
  }
  void poll() {                                   // call often; never blocks
    while (port_ && port_->available()) {
      char ch = (char)port_->read();
      if (ch == '\n' || ch == '\r') { if (len_ > 6) parse(); len_ = 0; }
      else if (len_ < sizeof(buf_) - 1) buf_[len_++] = ch;
      else len_ = 0;                              // overlong junk: restart
      buf_[len_] = 0;
    }
  }
  // GPS speed in m/s, NAN if there is no fix or the last fix is older than 2.5 s.
  float speedMps() const {
    if (!fix_ || millis() - lastFixMs_ > 2500) return NAN;
    return speed_;
  }
  bool hasFix() const { return fix_ && millis() - lastFixMs_ <= 2500; }
  uint32_t sentences() const { return good_; }   // >0 means the module is talking to us

 private:
  static uint8_t hex(char c) { return c >= 'A' ? (c - 'A' + 10) : (c - '0'); }
  void parse() {
    if (buf_[0] != '$') return;
    char* star = strchr(buf_, '*');
    if (!star || star - buf_ < 6) return;
    uint8_t cs = 0;
    for (char* p = buf_ + 1; p < star; p++) cs ^= (uint8_t)*p;
    if (cs != (uint8_t)((hex(star[1]) << 4) | hex(star[2]))) return;   // bad checksum
    good_++;
    if (strncmp(buf_ + 3, "RMC", 3) != 0) return;
    *star = 0;
    // fields: 0 $xxRMC, 1 time, 2 status, 3 lat, 4 N/S, 5 lon, 6 E/W, 7 speed(knots)
    char* f[9] = {0};
    int n = 0;
    for (char* p = buf_; n < 9;) {
      f[n++] = p;
      char* c = strchr(p, ',');
      if (!c) break;
      *c = 0; p = c + 1;
    }
    if (n < 8) return;
    fix_ = (f[2][0] == 'A');
    if (fix_ && f[7][0]) { speed_ = atof(f[7]) * 0.514444f; lastFixMs_ = millis(); }
  }
  HardwareSerial* port_ = nullptr;
  char buf_[128];
  size_t len_ = 0;
  bool fix_ = false;
  float speed_ = 0;
  uint32_t lastFixMs_ = 0, good_ = 0;
};
