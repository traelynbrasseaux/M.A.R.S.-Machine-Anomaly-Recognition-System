// Per-window features, mirroring host/mhm/features: same windows, bands, filters and order.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ml {

constexpr size_t kWindow = 1024;
constexpr size_t kHop = 512;
constexpr size_t kBins = kWindow / 2 + 1;
constexpr size_t kBands = 8;
constexpr size_t kPerAxis = kBands + 3;  // log band energies, RMS, kurtosis, crest
constexpr size_t kVibrationFeatures = 3 * kPerAxis;
constexpr size_t kMels = 32;
constexpr size_t kMfccs = 13;
constexpr size_t kAudioFeatures = kMels + kMfccs;
constexpr size_t kMaxFeatures = kAudioFeatures;
constexpr float kAccelFs = 3200.0f;
constexpr float kAudioFs = 16000.0f;

class FeatureExtractor {
 public:
  FeatureExtractor();
  // xyz holds kWindow interleaved x, y, z samples in raw counts.
  template <typename T>
  void vibration(const T* xyz, float fs, float* out);
  // x holds kWindow samples in int16 units.
  template <typename T>
  void audio(const T* x, float* out);

 private:
  void powerSpectrum();
  void setBandFs(float fs);

  float hann_[kWindow];
  float cos_[kWindow / 2];
  float sin_[kWindow / 2];
  float re_[kWindow];
  float im_[kWindow];
  float power_[kBins];
  float bandFs_ = 0.0f;
  size_t bandStart_[kBands];
  size_t bandEnd_[kBands];
  // Each bin sits in at most two mel filters, so 2 * kBins weights always fit.
  size_t melStart_[kMels];
  size_t melCount_[kMels];
  size_t melOffset_[kMels];
  float melWeights_[2 * kBins];
  float dct_[kMfccs][kMels];
};

}  // namespace ml
