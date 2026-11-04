// Command line wrapper around lib/ml for the PC, so host tests can check it against the Python.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>
#include <vector>

#include "detector.h"
#include "feature_extractor.h"
#include "frame.h"
#include "hdc.h"
#include "mahalanobis.h"

namespace {

template <typename T>
std::vector<T> readAll(const char* path) {
  FILE* f = fopen(path, "rb");
  if (!f) {
    fprintf(stderr, "cannot open %s\n", path);
    exit(2);
  }
  fseek(f, 0, SEEK_END);
  const long bytes = ftell(f);
  fseek(f, 0, SEEK_SET);
  std::vector<T> data(static_cast<size_t>(bytes) / sizeof(T));
  if (fread(data.data(), sizeof(T), data.size(), f) != data.size()) {
    fprintf(stderr, "short read on %s\n", path);
    exit(2);
  }
  fclose(f);
  return data;
}

template <typename T>
void writeAll(const char* path, const std::vector<T>& data) {
  FILE* f = fopen(path, "wb");
  if (!f || fwrite(data.data(), sizeof(T), data.size(), f) != data.size()) {
    fprintf(stderr, "cannot write %s\n", path);
    exit(2);
  }
  fclose(f);
}

// features <accel|audio> <fs> <in.f32> <out.f32>: windows of kWindow every kHop samples.
int features(char** argv) {
  static ml::FeatureExtractor fx;
  const std::string kind = argv[2];
  const float fs = static_cast<float>(atof(argv[3]));
  const std::vector<float> x = readAll<float>(argv[4]);
  const bool accel = kind == "accel";
  const size_t channels = accel ? 3 : 1;
  const size_t width = accel ? ml::kVibrationFeatures : ml::kAudioFeatures;
  const size_t n = x.size() / channels;
  const size_t windows = n < ml::kWindow ? 0 : 1 + (n - ml::kWindow) / ml::kHop;
  std::vector<float> out(windows * width);
  for (size_t w = 0; w < windows; w++) {
    const float* window = x.data() + w * ml::kHop * channels;
    if (accel) {
      fx.vibration(window, fs, &out[w * width]);
    } else {
      fx.audio(window, &out[w * width]);
    }
  }
  writeAll(argv[5], out);
  return 0;
}

// score <n> <stats.f32> <bundle.f32> <test.f32> <out.f32>: learn in the node's two phases, then
// write mahalanobis, hdc binary and hdc bipolar scores for each test window.
int score(char** argv) {
  static ml::ItemMemory memory;
  static ml::HdcModel hdc;
  static ml::MahalanobisModel mahalanobis;
  const size_t n = static_cast<size_t>(atoi(argv[2]));
  const std::vector<float> stats = readAll<float>(argv[3]);
  const std::vector<float> bundle = readAll<float>(argv[4]);
  const std::vector<float> test = readAll<float>(argv[5]);

  memory.generate(ml::kSeed);
  hdc.begin(n);
  mahalanobis.begin(n);
  for (size_t i = 0; i + n <= stats.size(); i += n) {
    hdc.observe(&stats[i]);
    mahalanobis.observe(&stats[i]);
  }
  hdc.finishRanges();
  for (size_t i = 0; i + n <= bundle.size(); i += n) {
    hdc.bundle(memory, &bundle[i]);
    mahalanobis.observe(&bundle[i]);
  }
  hdc.finish(memory);
  if (!mahalanobis.finish()) {
    fprintf(stderr, "covariance is not positive definite\n");
    return 3;
  }

  std::vector<float> out;
  for (size_t i = 0; i + n <= test.size(); i += n) {
    float binary = 0;
    float bipolar = 0;
    hdc.score(memory, &test[i], binary, bipolar);
    out.push_back(mahalanobis.score(&test[i]));
    out.push_back(binary);
    out.push_back(bipolar);
  }
  writeAll(argv[6], out);
  return 0;
}

// replay <accel|audio> <in.i16> <learn_s> <calibrate_s> <margin> <out.f32>: the firmware's own
// detector, fed in the firmware's block sizes and learning from t = 0. One row per finished block:
// t, phase, alerts, three scores, three thresholds.
int replay(char** argv) {
  static ml::ItemMemory memory;
  static ml::FeatureExtractor fx;
  const bool accel = std::string(argv[2]) == "accel";
  static ml::StreamDetector detector(accel ? ml::Kind::Accel : ml::Kind::Audio, memory, fx);
  const std::vector<int16_t> x = readAll<int16_t>(argv[3]);
  const size_t channels = accel ? 3 : 1;
  const size_t block = accel ? frame::kAccelSamples : frame::kAudioSamples;
  const float fs = accel ? ml::kAccelFs : ml::kAudioFs;

  memory.generate(ml::kSeed);
  detector.setMargin(static_cast<float>(atof(argv[6])));
  detector.learn(static_cast<float>(atof(argv[4])), static_cast<float>(atof(argv[5])));
  std::vector<float> out;
  ml::BlockResult r;
  for (size_t i = 0; (i + block) * channels <= x.size(); i += block) {
    if (!detector.push(&x[i * channels], block, r)) continue;
    out.push_back((i + block) / fs);
    out.push_back(static_cast<float>(r.phase));
    out.push_back(r.alerts);
    for (size_t m = 0; m < ml::kModels; m++) out.push_back(r.scores[m]);
    for (size_t m = 0; m < ml::kModels; m++) out.push_back(r.thresholds[m]);
  }
  writeAll(argv[7], out);
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 6 && strcmp(argv[1], "features") == 0) return features(argv);
  if (argc == 7 && strcmp(argv[1], "score") == 0) return score(argv);
  if (argc == 8 && strcmp(argv[1], "replay") == 0) return replay(argv);
  fprintf(stderr,
          "usage: ml_cli features <accel|audio> <fs> <in.f32> <out.f32>\n"
          "       ml_cli score <n> <stats.f32> <bundle.f32> <test.f32> <out.f32>\n"
          "       ml_cli replay <accel|audio> <in.i16> <learn_s> <calibrate_s> <margin> <out.f32>\n");
  return 1;
}
