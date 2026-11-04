// Squared Mahalanobis distance on z-scored features, mirroring host/mhm/models/mahalanobis.py.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "feature_extractor.h"

namespace ml {

constexpr double kRidge = 1e-3;

class MahalanobisModel {
 public:
  void begin(size_t nFeatures);
  void observe(const float* x);
  // Returns false if the covariance could not be inverted, which the ridge should prevent.
  bool finish();
  float score(const float* x) const;

 private:
  size_t n_ = 0;
  uint32_t count_ = 0;
  double sum_[kMaxFeatures];
  double cross_[kMaxFeatures * (kMaxFeatures + 1) / 2];  // upper triangle of sum(x x^T)
  double mean_[kMaxFeatures];
  double invStd_[kMaxFeatures];
  float precision_[kMaxFeatures * kMaxFeatures];
};

}  // namespace ml
