// HDC prototype scorer, mirroring host/mhm/models/hdc.py bit for bit.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "feature_extractor.h"

namespace ml {

constexpr size_t kDim = 4096;
constexpr size_t kWords = kDim / 32;
constexpr size_t kLevels = 16;
constexpr uint32_t kSeed = 0x1234ABCD;
constexpr double kRangeSigma = 6.0;

// All hypervectors from one LFSR stream, in the Python order: tiebreak, level base, thresholds, IDs.
struct ItemMemory {
  void generate(uint32_t seed);

  uint32_t tiebreak[kWords];
  uint32_t levels[kLevels][kWords];
  uint32_t ids[kMaxFeatures][kWords];
};

class HdcModel {
 public:
  void begin(size_t nFeatures);
  // Phase 1: feature ranges for the quantizer.
  void observe(const float* x);
  void finishRanges();
  // Phase 2: bundle windows into the prototype.
  void bundle(const ItemMemory& memory, const float* x);
  void finish(const ItemMemory& memory);
  // Hamming distance (binary) and cosine distance (bipolar) to the prototype.
  void score(const ItemMemory& memory, const float* x, float& binary, float& bipolar);

 private:
  void encode(const ItemMemory& memory, const float* x);
  bool majority(uint32_t ones, uint32_t votes, const ItemMemory& memory, size_t i) const;

  size_t n_ = 0;
  uint32_t observed_ = 0;
  uint32_t bundled_ = 0;
  double sum_[kMaxFeatures];
  double sumSq_[kMaxFeatures];
  double lo_[kMaxFeatures];
  double width_[kMaxFeatures];
  uint8_t ones_[kDim];          // bound vectors voting 1 at each position, for one window
  uint16_t votes_[kDim];        // bundled windows whose majority bit was 1, so at most 65535
  int32_t prototypeSum_[kDim];  // bipolar prototype
  uint32_t prototype_[kWords];  // binary prototype
  double prototypeNorm_ = 0;
};

}  // namespace ml
