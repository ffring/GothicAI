#pragma once

#include <Tempest/Vec>

#include <cstddef>
#include <cstdint>
#include <vector>

class Npc;

// Opt-in navigation sensor. It neither drives NPCs nor publishes bridge observations.
// Coordinates and ranges are centimetres; yaw/pitch are degrees relative to the posed head.
class NavigationEye final {
  public:
    enum class HitKind : uint8_t { None, World, StaticVob, MovableVob, Npc, Water };

    struct Config final {
      size_t   layout = 500; // 250 / 500 / 1000 select 238 / 500 / 1036 rays
      float    centerRangeCm = 10000.f;
      float    peripheralRangeCm = 3000.f;
      float    rangeMultiplier = 1.f; // reserved for darkness/fog; no weather policy here
      float    nearRangeCm = 1000.f;
      uint64_t nearPeriodMs = 100;
      uint64_t farPeriodMs = 500;
      };

    struct Ray final {
      float yawDeg = 0, pitchDeg = 0;
      float maxRangeCm = 0, distanceCm = 0, normalY = 0;
      HitKind kind = HitKind::None;
      Tempest::Vec3 origin = {}, direction = {}, point = {};
      uint64_t sampledAtMs = 0, nearSampledAtMs = 0;
      bool hit = false;
      // False until the unobstructed far segment has been observed at least once.
      // A cached far observation retains its own original head pose and timestamp.
      bool complete = false;
      };

    struct Update final {
      double costUs = 0;
      size_t casts = 0, nearCasts = 0, farCasts = 0;
      bool updated = false, farUpdated = false;
      };

    NavigationEye();
    explicit NavigationEye(const Config& config);
    Update update(const Npc& npc, uint64_t gameMs);
    void reset();
    const Config& config() const { return cfg; }
    const std::vector<Ray>& samples() const { return rays; }
    size_t rayCount() const { return rays.size(); }
    static const char* hitKindName(HitKind kind);

  private:
    struct CachedFar final {
      Ray ray;
      bool valid = false;
      };

    Config cfg;
    std::vector<Ray> rays;
    std::vector<Tempest::Vec3> localDirections;
    std::vector<CachedFar> far;
    uint64_t lastNear = 0, lastFar = 0;
    bool sampled = false;
  };
