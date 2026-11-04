// One sensor stream on the node: raw samples in, learning phases, one result per ~1 s block out.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "feature_extractor.h"
#include "hdc.h"
#include "mahalanobis.h"

namespace ml {

enum class Kind : uint8_t { Accel = 1, Audio = 2 };  // same ids as the frame streams
enum class Phase : uint8_t { Idle = 0, Ranges = 1, Bundle = 2, Calibrate = 3, Detect = 4 };

constexpr size_t kModels = 3;  // mahalanobis, hdc binary, hdc bipolar
constexpr float kBlockSeconds = 1.0f;
constexpr float kDefaultMargin = 1.2f;

struct BlockResult {
  Kind kind;
  Phase phase;
  uint8_t alerts;             // bit m set when model m scored over its threshold
  float scores[kModels];      // mean window score over the block; 0 while learning
  float thresholds[kModels];  // 0 until calibration ends
};

class StreamDetector {
 public:
  StreamDetector(Kind kind, const ItemMemory& memory, FeatureExtractor& features);
  // frames <= kHop; accel samples are interleaved x, y, z. True when out holds a finished block.
  bool push(const int16_t* samples, size_t frames, BlockResult& out);
  // First half of learnSeconds sets feature ranges, the second bundles; then calibration.
  void learn(float learnSeconds, float calibrateSeconds);
  bool detect();  // resume with the model already learned; false if there is none
  void stop();
  void setMargin(float margin);
  Phase phase() const { return phase_; }
  const float* thresholds() const { return thresholds_; }

 private:
  bool onWindow(BlockResult& out);
  void enter(Phase phase);
  void applyMargin();

  const Kind kind_;
  const size_t channels_;
  const size_t nFeatures_;
  const float fs_;
  const ItemMemory& memory_;
  FeatureExtractor& features_;
  HdcModel hdc_;
  MahalanobisModel mahalanobis_;

  int16_t ring_[kWindow * 3];
  int16_t window_[kWindow * 3];
  size_t ringPos_ = 0;
  size_t filled_ = 0;
  size_t sinceWindow_ = 0;
  float x_[kMaxFeatures];

  Phase phase_ = Phase::Idle;
  bool trained_ = false;
  bool calibrated_ = false;
  uint32_t bundleWindows_ = 0;
  uint32_t calibrateBlocks_ = 0;
  uint32_t left_ = 0;  // windows or blocks left in the current phase
  size_t blockWindows_;
  size_t inBlock_ = 0;
  double blockSum_[kModels];
  float calibrationMax_[kModels];
  float thresholds_[kModels];
  float margin_ = kDefaultMargin;
};

}  // namespace ml
