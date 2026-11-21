// roadcore - portable (no Arduino dependencies) event detector + physics classifier.
// A C++ port of roadrec/classifier.py. Runs on the ESP32 and on a laptop, so the same
// code can be checked against real recordings on the Mac before any hardware exists.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstddef>
#include <vector>

namespace rr {

constexpr int FS = 100;            // working sample rate, Hz (the sampler must match)
constexpr int PRE_N = 1000;        // 10 s kept before the trigger
constexpr int POST_N = 600;        // 6 s kept after the trigger
constexpr int WIN_N = PRE_N + POST_N;

struct Sample {
  float a[3];   // acceleration, g, gravity INCLUDED
  float w[3];   // angular rate, deg/s
  float speed;  // GPS speed in m/s, NAN when unknown
};

struct Cfg {
  // triggers
  float trig_impact_g = 2.0f;
  float trig_freefall_g = 0.50f;
  int   trig_freefall_ms = 80;
  float trig_horiz_g = 0.30f;
  int   trig_horiz_ms = 300;
  float trig_gyro_dps = 200.0f;
  float merge_gap_s = 2.0f;
  float gravity_tau_s = 8.0f;
  float warmup_s = 3.0f;
  // classification
  float crash_g = 4.0f;
  float pothole_max_ms = 100.0f;
  float pothole_vert_frac = 0.55f;
  float lying_deg = 45.0f;
  float upright_deg = 20.0f;
  float stopped_mps = 1.5f;
  float still_acc_std_g = 0.10f;
  float still_gyro_dps = 20.0f;
  float drop_freefall_ms = 120.0f;
  float brake_g = 0.35f;
  float brake_ms = 400.0f;
  float swerve_dps = 35.0f;
  float brake_min_mps = 2.0f;
  float drop_quiet_s = 15.0f;
};

enum class Label : uint8_t {
  NONE, DROP, CRASH, FALL, POTHOLE, HARD_BRAKE, NEAR_MISS,
  HANDLING, MINOR_IMPACT, BUMP, INCOMPLETE
};
const char* labelName(Label l);
bool isAlert(Label l);  // CRASH / FALL / NEAR_MISS / DROP, the same set evaluate.py counts

struct Features {
  int i0 = 0, i_peak = 0;
  float g_pre[3] = {0, 0, 1};
  float peak_g = 0, spike_ms = 0, vert_frac = 0;
  float gyro_peak = 0, yaw_peak = 0, freefall_ms = 0;
  float horiz_peak_g = 0, horiz_ms = 0;
  bool post_ok = false, post_still = false;
  float post_tilt_deg = 0;
  float speed_pre = NAN, speed_post = NAN;
};

struct Result {
  Label label = Label::NONE;
  Features f;
  char reason[200] = {0};
};

// Pure functions: classify one frozen window. i0 = index of the first trigger sample.
Features computeFeatures(const Sample* w, int n, int i0, const Cfg& c);
Result classify(const Features& f, const Cfg& c);
Result analyseWindow(const Sample* w, int n, int i0, const Cfg& c = Cfg());

// Streaming detector: feed one sample every 10 ms. push() returns true when a complete
// event window (10 s before, 6 s after the trigger) is ready to read.
class Detector {
 public:
  explicit Detector(const Cfg& c = Cfg());
  bool push(const Sample& s);
  const Sample* window() const { return win_.data(); }
  int windowLen() const { return winLen_; }
  int triggerIndex() const { return winTrig_; }                 // trigger index inside window()
  uint32_t triggerSampleCount() const { return winTrigAbs_; }   // global sample number
  // End of data / power-loss warning: if an event is mid-capture, freeze what exists so far.
  // Returns true when a (possibly shorter) window is ready to read, exactly as push() does.
  bool flush();
  Result finishEvent();   // classify the window just returned; arms the post-DROP quiet period
  uint32_t samplesSeen() const { return n_; }

 private:
  void onTrigger(int64_t startAbs);
  bool freeze(int64_t endAbs);
  void startPending();    // begin the capture for a trigger that arrived while another capture was running
  Cfg c_;
  std::vector<Sample> ring_, win_;
  uint32_t n_ = 0;          // samples pushed so far
  float g_[3] = {0, 0, 1};  // slow gravity estimate
  float gSum_[3] = {0, 0, 0};
  bool gInit_ = false;
  int ffRun_ = 0, hRun_ = 0;
  float hBuf_[10] = {0};
  int hIdx_ = 0;
  bool capturing_ = false;
  int64_t firstAbs_ = 0, i0Abs_ = 0;
  int remaining_ = 0;
  // Triggers (more than merge_gap_s after the previous one) that arrive while a capture is
  // still running are queued here and captured next, so a crash shortly after a bump is not lost.
  static constexpr int MAX_PENDING = 4;
  int64_t pend_[MAX_PENDING] = {0, 0, 0, 0};
  int pendCnt_ = 0;
  int64_t lastTrigAbs_ = -1000000;
  int64_t quietUntil_ = -1;
  int winLen_ = 0, winTrig_ = 0;
  uint32_t winTrigAbs_ = 0;
};

}  // namespace rr
