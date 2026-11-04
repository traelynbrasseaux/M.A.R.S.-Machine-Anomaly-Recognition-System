#include "feature_extractor.h"

#include <math.h>

namespace ml {

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kEps = 1e-12;
constexpr double kBandEdges[kBands + 1] = {5, 15, 35, 60, 100, 200, 400, 800, 1600};
constexpr double kMelMinHz = 50.0;
constexpr double kMelMaxHz = 8000.0;

double hzToMel(double f) { return 2595.0 * log10(1.0 + f / 700.0); }

double melToHz(double m) { return 700.0 * (pow(10.0, m / 2595.0) - 1.0); }

}  // namespace

FeatureExtractor::FeatureExtractor() {
  for (size_t i = 0; i < kWindow; i++) {
    hann_[i] = static_cast<float>(0.5 - 0.5 * cos(2 * kPi * i / kWindow));
  }
  for (size_t k = 0; k < kWindow / 2; k++) {
    cos_[k] = static_cast<float>(cos(2 * kPi * k / kWindow));
    sin_[k] = static_cast<float>(sin(2 * kPi * k / kWindow));
  }

  // kMels + 2 evenly spaced mel points; filter m rises from point m to m + 1 and falls to m + 2.
  const double melLo = hzToMel(kMelMinHz);
  const double melStep = (hzToMel(kMelMaxHz) - melLo) / (kMels + 1);
  const double binHz = kAudioFs / kWindow;
  size_t offset = 0;
  for (size_t m = 0; m < kMels; m++) {
    const double lo = melToHz(melLo + melStep * m);
    const double center = melToHz(melLo + melStep * (m + 1));
    const double hi = melToHz(melLo + melStep * (m + 2));
    melOffset_[m] = offset;
    melStart_[m] = 0;
    melCount_[m] = 0;
    for (size_t k = 0; k < kBins; k++) {
      const double f = k * binHz;
      const double w = fmax(0.0, fmin((f - lo) / (center - lo), (hi - f) / (hi - center)));
      if (w <= 0.0) continue;
      if (melCount_[m] == 0) melStart_[m] = k;
      melWeights_[offset++] = static_cast<float>(w);
      melCount_[m]++;
    }
  }

  for (size_t c = 0; c < kMfccs; c++) {
    for (size_t m = 0; m < kMels; m++) {
      const double basis = cos(kPi * c * (2 * m + 1) / (2 * kMels)) * sqrt(2.0 / kMels);
      dct_[c][m] = static_cast<float>(c == 0 ? basis / sqrt(2.0) : basis);
    }
  }
}

void FeatureExtractor::setBandFs(float fs) {
  if (fs == bandFs_) return;
  bandFs_ = fs;
  for (size_t b = 0; b < kBands; b++) {
    bandStart_[b] = kBins;
    bandEnd_[b] = kBins;
    for (size_t k = 0; k < kBins; k++) {
      const double f = static_cast<double>(k) * fs / kWindow;
      if (bandStart_[b] == kBins && f >= kBandEdges[b]) bandStart_[b] = k;
      if (f >= kBandEdges[b + 1]) {
        bandEnd_[b] = k;
        break;
      }
    }
  }
}

// Hann-windowed |DFT|^2 of re_, by an in-place radix-2 FFT; the RTL will use the same structure.
void FeatureExtractor::powerSpectrum() {
  for (size_t i = 0; i < kWindow; i++) {
    re_[i] *= hann_[i];
    im_[i] = 0.0f;
  }
  for (size_t i = 1, j = 0; i < kWindow; i++) {
    size_t bit = kWindow >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      const float t = re_[i];
      re_[i] = re_[j];
      re_[j] = t;
    }
  }
  for (size_t len = 2; len <= kWindow; len <<= 1) {
    const size_t stride = kWindow / len;
    for (size_t start = 0; start < kWindow; start += len) {
      for (size_t k = 0; k < len / 2; k++) {
        const float wr = cos_[k * stride];
        const float wi = -sin_[k * stride];
        const size_t a = start + k;
        const size_t b = a + len / 2;
        const float tr = re_[b] * wr - im_[b] * wi;
        const float ti = re_[b] * wi + im_[b] * wr;
        re_[b] = re_[a] - tr;
        im_[b] = im_[a] - ti;
        re_[a] += tr;
        im_[a] += ti;
      }
    }
  }
  for (size_t k = 0; k < kBins; k++) power_[k] = re_[k] * re_[k] + im_[k] * im_[k];
}

template <typename T>
void FeatureExtractor::vibration(const T* xyz, float fs, float* out) {
  setBandFs(fs);
  for (size_t axis = 0; axis < 3; axis++) {
    double sum = 0;
    for (size_t i = 0; i < kWindow; i++) sum += static_cast<double>(xyz[3 * i + axis]);
    const float mean = static_cast<float>(sum / kWindow);

    double m2 = 0;
    double m4 = 0;
    float peak = 0;
    for (size_t i = 0; i < kWindow; i++) {
      const float v = static_cast<float>(xyz[3 * i + axis]) - mean;
      const double v2 = static_cast<double>(v) * v;
      m2 += v2;
      m4 += v2 * v2;
      peak = fmaxf(peak, fabsf(v));
      re_[i] = v;
    }
    powerSpectrum();

    float* o = out + axis * kPerAxis;
    for (size_t b = 0; b < kBands; b++) {
      double energy = 0;
      for (size_t k = bandStart_[b]; k < bandEnd_[b]; k++) energy += power_[k];
      o[b] = static_cast<float>(log10(energy + kEps));
    }
    const double var = m2 / kWindow;
    const double rms = sqrt(var);
    o[kBands] = static_cast<float>(rms);
    o[kBands + 1] = static_cast<float>((m4 / kWindow) / fmax(var * var, kEps));
    o[kBands + 2] = static_cast<float>(peak / fmax(rms, kEps));
  }
}

template <typename T>
void FeatureExtractor::audio(const T* x, float* out) {
  double sum = 0;
  for (size_t i = 0; i < kWindow; i++) sum += static_cast<double>(x[i]);
  const float mean = static_cast<float>(sum / kWindow / 32768.0);
  for (size_t i = 0; i < kWindow; i++) re_[i] = static_cast<float>(x[i]) / 32768.0f - mean;
  powerSpectrum();

  float logMel[kMels];
  for (size_t m = 0; m < kMels; m++) {
    double energy = 0;
    const float* w = melWeights_ + melOffset_[m];
    for (size_t j = 0; j < melCount_[m]; j++) energy += w[j] * power_[melStart_[m] + j];
    logMel[m] = static_cast<float>(log10(energy + kEps));
    out[m] = logMel[m];
  }
  for (size_t c = 0; c < kMfccs; c++) {
    double acc = 0;
    for (size_t m = 0; m < kMels; m++) acc += dct_[c][m] * logMel[m];
    out[kMels + c] = static_cast<float>(acc);
  }
}

template void FeatureExtractor::vibration<int16_t>(const int16_t*, float, float*);
template void FeatureExtractor::vibration<float>(const float*, float, float*);
template void FeatureExtractor::audio<int16_t>(const int16_t*, float*);
template void FeatureExtractor::audio<float>(const float*, float*);

}  // namespace ml
