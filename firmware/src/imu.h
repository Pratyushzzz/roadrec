// Minimal MPU6050 driver (also works with most GY-521 clones). +/-16 g, +/-2000 dps.
#pragma once
#include <Arduino.h>
#include <Wire.h>

class Imu {
 public:
  bool begin(TwoWire& wire, uint8_t addr = 0x68) {
    w_ = &wire; addr_ = addr;
    w_->beginTransmission(addr_);
    if (w_->endTransmission() != 0) return false;        // no ACK: wiring or address
    if (!wr(0x6B, 0x80)) return false;                   // reset
    delay(100);
    if (!wr(0x6B, 0x01)) return false;                   // wake, clock = gyro X PLL
    delay(10);
    // DLPF 184 Hz keeps short pothole/impact spikes; 1 kHz internal rate, we read at 100 Hz.
    return wr(0x19, 0x00) && wr(0x1A, 0x01) && wr(0x1B, 0x18) && wr(0x1C, 0x18);
  }
  // a in g (gravity included), g in deg/s. Returns false on an I2C error.
  bool read(float a[3], float g[3]) {
    w_->beginTransmission(addr_);
    w_->write(0x3B);
    if (w_->endTransmission(false) != 0) return false;
    if (w_->requestFrom((int)addr_, 14) != 14) return false;
    int16_t v[7];
    for (int i = 0; i < 7; i++) {
      uint8_t hi = w_->read(), lo = w_->read();
      v[i] = (int16_t)((hi << 8) | lo);
    }
    for (int k = 0; k < 3; k++) { a[k] = v[k] / 2048.0f; g[k] = v[4 + k] / 16.4f; }  // v[3] = temperature
    return true;
  }
  bool clipped(const float a[3]) const {                 // MPU6050 saturates at 16 g
    return fabsf(a[0]) > 15.9f || fabsf(a[1]) > 15.9f || fabsf(a[2]) > 15.9f;
  }

 private:
  bool wr(uint8_t reg, uint8_t val) {
    w_->beginTransmission(addr_);
    w_->write(reg); w_->write(val);
    return w_->endTransmission() == 0;
  }
  TwoWire* w_ = nullptr;
  uint8_t addr_ = 0x68;
};
