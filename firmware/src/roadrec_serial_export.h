// roadrec_serial_export.h
// Streams saved event files to the website over the USB serial port, and answers its commands.
//
// Wire format (what the website listens for):
//   [evt-begin] evt_0007_CRASH.csv
//   t,ax,ay,az,gx,gy,gz,speed          <- the file, byte for byte (LF line endings)
//   ...
//   [evt-end] <64 hex chars: SHA-256 of the file bytes>
//
// Commands the website sends:
//   LIST          -> [list-begin] / [file] <name> <bytes> ... / [list-end]
//   GET <name>    -> the same [evt-begin] ... [evt-end] block
#pragma once
#include <Arduino.h>
#include <SD.h>
#include <string.h>
#include "mbedtls/sha256.h"

inline bool rrSendEvent(const char* path, const char* name) {
  File f = SD.open(path, FILE_READ);
  if (!f) return false;

  // main.cpp sets Serial.setTxTimeoutMs(0) so a missing USB host never stalls the sampler.
  // With a host connected that would drop bytes in a long transfer, so wait a little while exporting.
  Serial.setTxTimeoutMs(200);

  mbedtls_sha256_context ctx;
  mbedtls_sha256_init(&ctx);
  mbedtls_sha256_starts(&ctx, 0);

  Serial.printf("\n[evt-begin] %s\n", name);
  uint8_t buf[256];
  size_t n, last = '\n';
  while ((n = f.read(buf, sizeof buf)) > 0) {
    mbedtls_sha256_update(&ctx, buf, n);
    Serial.write(buf, n);
    last = buf[n - 1];
  }
  f.close();
  if (last != '\n') Serial.write('\n');   // keep [evt-end] on its own line (file must end with LF)

  uint8_t h[32];
  mbedtls_sha256_finish(&ctx, h);
  mbedtls_sha256_free(&ctx);
  Serial.print("[evt-end] ");
  for (int i = 0; i < 32; i++) Serial.printf("%02x", h[i]);
  Serial.println();

  Serial.setTxTimeoutMs(0);               // back to never blocking the sampler
  return true;
}

// Call once per pass of writerTask (core 0), so the SD card still has one user at a time.
inline void rrHandleSerialCommands() {
  static char line[80];
  static size_t n = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c != '\n') { if (n < sizeof line - 1) line[n++] = c; continue; }
    line[n] = 0;
    n = 0;
    if (!strcmp(line, "LIST")) {
      Serial.println("\n[list-begin]");
      File d = SD.open("/events");
      if (d) {
        for (File f = d.openNextFile(); f; f = d.openNextFile()) {
          const char* nm = f.name();
          size_t L = strlen(nm);
          if (L > 4 && !strcmp(nm + L - 4, ".csv")) Serial.printf("[file] %s %u\n", nm, (unsigned)f.size());
          f.close();
        }
        d.close();
      }
      Serial.println("[list-end]");
    } else if (!strncmp(line, "GET ", 4)) {
      const char* nm = line + 4;
      if (strchr(nm, '/') || strstr(nm, "..")) { Serial.println("[err] bad name"); continue; }
      char path[96];
      snprintf(path, sizeof path, "/events/%s", nm);
      if (!rrSendEvent(path, nm)) Serial.println("[err] not found");
    }
  }
}