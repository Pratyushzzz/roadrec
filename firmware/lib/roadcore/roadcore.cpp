// roadcore.cpp - C++ port of roadrec/classifier.py (see roadcore.h)
#include "roadcore.h"
#include <algorithm>
#include <cstdio>

namespace rr {

// ------------------------------------------------------------- small helpers
static inline float norm3(const float* v) { return sqrtf(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); }
static inline float dot3(const float* a, const float* b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }

static float medianOf(std::vector<float>& v) {  // same as numpy.median
  if (v.empty()) return NAN;
  std::sort(v.begin(), v.end());
  size_t n = v.size();
  return (n % 2) ? v[n / 2] : 0.5f * (v[n / 2 - 1] + v[n / 2]);
}

static float nanMedianSpeed(const Sample* w, int a, int b) {  // [a, b), NaN-aware
  std::vector<float> v;
  for (int i = a; i < b; i++)
    if (!std::isnan(w[i].speed)) v.push_back(w[i].speed);
  return medianOf(v);
}

static float angleDeg(const float* u, const float* v) {
  float c = dot3(u, v) / (norm3(u) * norm3(v) + 1e-9f);
  c = std::max(-1.0f, std::min(1.0f, c));
  return acosf(c) * 180.0f / (float)M_PI;
}

const char* labelName(Label l) {
  switch (l) {
    case Label::DROP: return "DROP";
    case Label::CRASH: return "CRASH";
    case Label::FALL: return "FALL";
    case Label::POTHOLE: return "POTHOLE";
    case Label::HARD_BRAKE: return "HARD_BRAKE";
    case Label::NEAR_MISS: return "NEAR_MISS";
    case Label::HANDLING: return "HANDLING";
    case Label::MINOR_IMPACT: return "MINOR_IMPACT";
    case Label::BUMP: return "BUMP";
    case Label::INCOMPLETE: return "INCOMPLETE";
    default: return "NONE";
  }
}

bool isAlert(Label l) {
  return l == Label::CRASH || l == Label::FALL || l == Label::NEAR_MISS || l == Label::DROP;
}

// numpy.convolve(x, ones(10)/10, 'same') : out[j] = mean(x[j-5 .. j+4]) with zero padding
static void smooth10(const std::vector<float>& x, std::vector<float>& out) {
  int L = (int)x.size();
  out.assign(L, 0.0f);
  for (int j = 0; j < L; j++) {
    float s = 0;
    for (int k = j - 5; k <= j + 4; k++)
      if (k >= 0 && k < L) s += x[k];
    out[j] = s / 10.0f;
  }
}

// -------------------------------------------------------------- features
Features computeFeatures(const Sample* w, int n, int i0, const Cfg& c) {
  Features f;
  f.i0 = i0;

  // pre-event gravity baseline (median of the window 3 s .. 0.3 s before the trigger)
  int lo = std::max(0, i0 - 300);
  int hiPre = std::max(lo + 1, i0 - 30);
  float gpre[3];
  if (hiPre - lo >= 30) {
    for (int k = 0; k < 3; k++) {
      std::vector<float> v;
      for (int i = lo; i < hiPre; i++) v.push_back(w[i].a[k]);
      gpre[k] = medianOf(v);
    }
  } else {
    int j = std::max(0, i0 - 1);
    for (int k = 0; k < 3; k++) gpre[k] = w[j].a[k];
  }
  float gn = norm3(gpre);
  float ghat[3] = {gpre[0] / gn, gpre[1] / gn, gpre[2] / gn};
  for (int k = 0; k < 3; k++) f.g_pre[k] = gpre[k];

  // impact spike
  int s0 = std::max(0, i0 - 10), s1 = std::min(n, i0 + 200);
  int L = s1 - s0;
  std::vector<float> vert(L), tot(L);
  for (int j = 0; j < L; j++) {
    float d[3] = {w[s0 + j].a[0] - gpre[0], w[s0 + j].a[1] - gpre[1], w[s0 + j].a[2] - gpre[2]};
    vert[j] = dot3(d, ghat);
    tot[j] = norm3(d);
  }
  int ipk = 0;
  for (int j = 1; j < L; j++)
    if (tot[j] > tot[ipk]) ipk = j;
  f.peak_g = tot[ipk];
  f.i_peak = s0 + ipk;
  float half = f.peak_g * 0.5f;
  int l = ipk, r = ipk;
  while (l > 0 && tot[l - 1] > half) l--;
  while (r < L - 1 && tot[r + 1] > half) r++;
  f.spike_ms = (float)(r - l + 1) * 1000.0f / FS;
  float sv = 0, st = 0;
  for (int j = l; j <= r; j++) { sv += vert[j] * vert[j]; st += tot[j] * tot[j]; }
  f.vert_frac = sv / (st + 1e-9f);

  // rotation
  int we = std::min(n, i0 + 300);
  for (int i = s0; i < we; i++) {
    f.gyro_peak = std::max(f.gyro_peak, norm3(w[i].w));
    f.yaw_peak = std::max(f.yaw_peak, fabsf(dot3(w[i].w, ghat)));
  }

  // free-fall: longest run of |a| < threshold in [i0-30, i0+100)
  {
    int a = std::max(0, i0 - 30), b = std::min(n, i0 + 100);
    int best = 0, cur = 0;
    for (int i = a; i < b; i++) {
      if (norm3(w[i].a) < c.trig_freefall_g) { cur++; best = std::max(best, cur); }
      else cur = 0;
    }
    f.freefall_ms = (float)best * 1000.0f / FS;
  }

  // sustained horizontal acceleration (smoothed over 0.1 s)
  {
    int e = std::min(n, i0 + 250);
    int M = e - s0;
    std::vector<float> hor(M), hs;
    for (int j = 0; j < M; j++) {
      float d[3] = {w[s0 + j].a[0] - gpre[0], w[s0 + j].a[1] - gpre[1], w[s0 + j].a[2] - gpre[2]};
      float vv = dot3(d, ghat);
      float h[3] = {d[0] - vv * ghat[0], d[1] - vv * ghat[1], d[2] - vv * ghat[2]};
      hor[j] = norm3(h);
    }
    smooth10(hor, hs);
    int cnt = 0;
    float mx = 0;
    for (int j = 0; j < M; j++) {
      mx = std::max(mx, hs[j]);
      if (hs[j] > c.brake_g) cnt++;
    }
    f.horiz_peak_g = mx;
    f.horiz_ms = (float)cnt * 1000.0f / FS;
  }

  // what happened afterwards (1 s .. 3 s after the peak)
  int pa = f.i_peak + 100, pb = std::min(n, f.i_peak + 300);
  f.post_ok = (pb - pa) >= 60;
  if (f.post_ok) {
    float med[3];
    for (int k = 0; k < 3; k++) {
      std::vector<float> v;
      for (int i = pa; i < pb; i++) v.push_back(w[i].a[k]);
      med[k] = medianOf(v);
    }
    f.post_tilt_deg = angleDeg(med, gpre);
    int m = pb - pa;
    double mean = 0, gm = 0;
    for (int i = pa; i < pb; i++) { mean += norm3(w[i].a); gm += norm3(w[i].w); }
    mean /= m; gm /= m;
    double var = 0;
    for (int i = pa; i < pb; i++) { double d = norm3(w[i].a) - mean; var += d * d; }
    float accStd = (float)sqrt(var / m);
    f.post_still = accStd < c.still_acc_std_g && (float)gm < c.still_gyro_dps;
  }

  f.speed_pre = nanMedianSpeed(w, std::max(0, i0 - 200), std::max(1, i0 - 20));
  f.speed_post = (f.i_peak + 200 < n) ? nanMedianSpeed(w, f.i_peak + 200, std::min(n, f.i_peak + 400)) : NAN;
  return f;
}

// ----------------------------------------------------------------- rules
Result classify(const Features& f, const Cfg& c) {
  Result R;
  R.f = f;
  char* o = R.reason;
  const size_t N = sizeof(R.reason);
  bool hasPre = !std::isnan(f.speed_pre), hasPost = !std::isnan(f.speed_post);
  bool known = hasPre && hasPost;

  // 0. dropped during installation: free-fall while not riding (needs no post-event data)
  if (f.freefall_ms >= c.drop_freefall_ms && !(hasPre && f.speed_pre >= 2.0f)) {
    R.label = Label::DROP;
    snprintf(o, N, "free-fall %.0f ms (>= %.0f), vehicle not moving", f.freefall_ms, c.drop_freefall_ms);
    return R;
  }
  if (!f.post_ok) {
    R.label = Label::INCOMPLETE;
    snprintf(o, N, "not enough data after the event to judge the outcome");
    return R;
  }

  bool lying = f.post_tilt_deg >= c.lying_deg;
  bool upright = f.post_tilt_deg <= c.upright_deg;
  bool stopped = known ? (f.speed_post < c.stopped_mps) : f.post_still;
  bool continued = known ? (f.speed_pre >= 2.0f && f.speed_post >= 0.5f * f.speed_pre) : !f.post_still;
  bool movingBefore = !hasPre || f.speed_pre >= 2.0f;

  if (f.peak_g >= c.crash_g && movingBefore && (lying || stopped)) {
    R.label = Label::CRASH;
    snprintf(o, N, "impact %.1f g (>= %.1f); orientation changed %.0f deg%s; %s", f.peak_g, c.crash_g,
             f.post_tilt_deg, lying ? " (vehicle down)" : "", stopped ? "stopped/motionless after" : "still moving");
    return R;
  }
  if (lying && !stopped) {
    R.label = Label::HANDLING;
    snprintf(o, N, "orientation changed %.0f deg but device kept moving afterwards (carried/hand-held), not a vehicle lying still",
             f.post_tilt_deg);
    return R;
  }
  if (lying) {
    R.label = Label::FALL;
    snprintf(o, N, "orientation changed %.0f deg and stays; peak only %.1f g (< %.1f), low-energy tip-over",
             f.post_tilt_deg, f.peak_g, c.crash_g);
    return R;
  }
  if (f.peak_g >= c.trig_impact_g && f.spike_ms <= c.pothole_max_ms && f.vert_frac >= c.pothole_vert_frac &&
      upright && continued) {
    R.label = Label::POTHOLE;
    snprintf(o, N, "spike %.0f ms, %.0f%% of energy along gravity, upright (%.0f deg), ride continued",
             f.spike_ms, f.vert_frac * 100.0f, f.post_tilt_deg);
    return R;
  }

  bool swerve = f.yaw_peak >= c.swerve_dps;
  bool hard = f.horiz_ms >= c.brake_ms || f.horiz_peak_g >= 0.7f;
  if (f.peak_g < c.crash_g && upright && hard && !(hasPre && f.speed_pre >= c.brake_min_mps)) {
    R.label = Label::HANDLING;
    snprintf(o, N, "hard deceleration/rotation but vehicle not confirmed moving (GPS speed < %.0f m/s or missing)",
             c.brake_min_mps);
    return R;
  }
  if (f.peak_g < c.crash_g && upright && hard) {
    if (swerve || f.horiz_peak_g >= 0.7f) {
      R.label = Label::NEAR_MISS;
      snprintf(o, N, "hard deceleration %.2f g, %s, no impact, ride continued", f.horiz_peak_g,
               swerve ? "with swerve" : "very hard braking");
    } else {
      R.label = Label::HARD_BRAKE;
      snprintf(o, N, "sustained horizontal decel %.2f g for %.0f ms, no swerve, no impact", f.horiz_peak_g,
               f.horiz_ms);
    }
    return R;
  }
  if (f.peak_g >= c.crash_g && continued) {
    R.label = Label::MINOR_IMPACT;
    snprintf(o, N, "%.1f g impact but upright and still moving", f.peak_g);
    return R;
  }
  R.label = Label::BUMP;
  snprintf(o, N, "trigger below every event signature");
  return R;
}

Result analyseWindow(const Sample* w, int n, int i0, const Cfg& c) {
  return classify(computeFeatures(w, n, i0, c), c);
}

// ------------------------------------------------------- streaming detector
Detector::Detector(const Cfg& c) : c_(c) {
  ring_.assign(WIN_N + 32, Sample{{0, 0, 1}, {0, 0, 0}, (float)NAN});
  win_.assign(WIN_N, Sample{{0, 0, 1}, {0, 0, 0}, (float)NAN});
}

void Detector::onTrigger(int64_t startAbs) {
  if (startAbs < (int64_t)(c_.warmup_s * FS)) return;               // no baseline yet
  bool newGroup = (startAbs - lastTrigAbs_) > (int64_t)(c_.merge_gap_s * FS);
  if (startAbs > lastTrigAbs_) lastTrigAbs_ = startAbs;              // chain merged triggers
  if (capturing_) {
    if (pendCnt_ > 0) {                                              // belongs to the queued (latest) group
      if (newGroup) {
        if (pendCnt_ < MAX_PENDING) pend_[pendCnt_++] = startAbs;
      } else if (startAbs < pend_[pendCnt_ - 1] && startAbs > i0Abs_ + (int64_t)(c_.merge_gap_s * FS)) {
        pend_[pendCnt_ - 1] = startAbs;                              // a run that began earlier
      }
      return;
    }
    if (newGroup) {                                                  // new event while busy: queue it
      pend_[pendCnt_++] = startAbs;
      return;
    }
    if (startAbs < i0Abs_) i0Abs_ = startAbs;                        // a run that began earlier
    return;
  }
  if (!newGroup) return;
  if (startAbs < quietUntil_) return;                                // device is being handled after a drop
  capturing_ = true;
  firstAbs_ = (int64_t)n_ - 1;                                       // current sample
  i0Abs_ = startAbs;
  remaining_ = POST_N - 1;
}

void Detector::startPending() {
  // take the oldest queued trigger; drop it if the device is in the post-DROP quiet period
  while (pendCnt_ > 0) {
    int64_t p = pend_[0];
    for (int k = 1; k < pendCnt_; k++) pend_[k - 1] = pend_[k];
    pendCnt_--;
    if (p < quietUntil_) continue;
    capturing_ = true;
    firstAbs_ = p;
    i0Abs_ = p;
    remaining_ = (int)(p + POST_N - (int64_t)n_) + 1;                // samples still to come (+1: this push also decrements)
    if (remaining_ < 1) remaining_ = 1;
    return;
  }
}

bool Detector::push(const Sample& s) {
  int64_t i = (int64_t)n_;
  ring_[n_ % ring_.size()] = s;
  n_++;
  if (!capturing_ && pendCnt_ > 0) startPending();

  // slow gravity tracker (learns only when |a| ~ 1 g, so impacts don't contaminate it)
  float mag = norm3(s.a);
  if (!gInit_) {
    for (int k = 0; k < 3; k++) gSum_[k] += s.a[k];
    for (int k = 0; k < 3; k++) g_[k] = gSum_[k] / (float)n_;
    if (n_ >= 100) gInit_ = true;
  } else if (mag > 0.9f && mag < 1.1f) {
    float alpha = 1.0f / (c_.gravity_tau_s * FS);
    for (int k = 0; k < 3; k++) g_[k] += alpha * (s.a[k] - g_[k]);
  }
  float gn = norm3(g_);
  float gh[3] = {g_[0] / gn, g_[1] / gn, g_[2] / gn};
  float dyn[3] = {s.a[0] - g_[0], s.a[1] - g_[1], s.a[2] - g_[2]};
  float vv = dot3(dyn, gh);
  float hv[3] = {dyn[0] - vv * gh[0], dyn[1] - vv * gh[1], dyn[2] - vv * gh[2]};
  float hor = norm3(hv), adyn = norm3(dyn);

  // 1. impact
  if (adyn > c_.trig_impact_g) onTrigger(i);

  // 2. free-fall run (reported at its start)
  int ffN = c_.trig_freefall_ms * FS / 1000;
  if (mag < c_.trig_freefall_g) {
    if (++ffRun_ == ffN) onTrigger(i - ffN + 1);
  } else ffRun_ = 0;

  // 3. sustained horizontal acceleration (causal 0.1 s average; run start corrected for its 4-sample lag)
  hBuf_[hIdx_] = hor;
  hIdx_ = (hIdx_ + 1) % 10;
  float hs = 0;
  for (int k = 0; k < 10; k++) hs += hBuf_[k];
  hs /= 10.0f;
  int hN = c_.trig_horiz_ms * FS / 1000;
  if (hs > c_.trig_horiz_g) {
    if (++hRun_ == hN) onTrigger(std::max<int64_t>(0, i - hN + 1 - 4));
  } else hRun_ = 0;

  // 4. fast rotation
  if (norm3(s.w) > c_.trig_gyro_dps) onTrigger(i);

  // window complete?
  if (capturing_) {
    if (n_ - 1 > (uint32_t)firstAbs_ && --remaining_ <= 0) {
      return freeze((int64_t)firstAbs_ + POST_N);
    }
  }
  return false;
}

bool Detector::freeze(int64_t endAbs) {  // endAbs exclusive
  int64_t startAbs = std::max<int64_t>(0, firstAbs_ - PRE_N);
  winLen_ = (int)(endAbs - startAbs);
  for (int k = 0; k < winLen_; k++) win_[k] = ring_[(uint64_t)(startAbs + k) % ring_.size()];
  winTrig_ = (int)(i0Abs_ - startAbs);
  winTrigAbs_ = (uint32_t)i0Abs_;
  capturing_ = false;
  return true;
}

bool Detector::flush() {
  if (!capturing_ && pendCnt_ > 0) startPending();
  if (!capturing_) return false;
  return freeze((int64_t)n_);
}

Result Detector::finishEvent() {
  Result r = analyseWindow(win_.data(), winLen_, winTrig_, c_);
  if (r.label == Label::DROP) {
    int64_t startAbs = (int64_t)winTrigAbs_ - winTrig_;
    quietUntil_ = startAbs + r.f.i_peak + (int64_t)(c_.drop_quiet_s * FS);
  }
  return r;
}

}  // namespace rr
