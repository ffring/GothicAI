#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

class Npc;

// Passive CPU observations shared by the native runner and the ordinary AI bridge. All entry
// points belong to the simulation thread. This object never acquires control of an NPC.
class BodyObservations final {
  public:
    struct Config final {
      uint64_t periodMs = 50;
      size_t maxActors = 4;
      size_t queueCapacity = 256;
      float rayRangeCm = 300;
      };

    // Generation changes invalidate runtime entity IDs, history, events and queued rows.
    static void reset(uint64_t generation);
    static void configure(const Config& config);
    static void watch(const std::vector<Npc*>& actors);
    static void disable();
    static bool enabled();
    static size_t watchedCount();
    static size_t queuedCount();
    static void setSink(std::function<void(std::string_view)> sink);
    static uint64_t lastStepCostUs();
    static size_t lastStepActorCount();
    static uint64_t entityId(const Npc& actor);
    static void forget(const Npc* actor);
    // Explicit teleport/fixture relocation; ordinary MoveAlgo correction is not a discontinuity.
    static void discontinuity(const Npc& actor);

    static void beginStep(uint64_t simStep);
    static uint64_t currentStep();
    // Call once after world movement and animation updates, before benchmark mutations.
    static void sampleAll();
    static void sample(Npc& actor, uint64_t gameMs);
    // Native hooks carry a fixed event vocabulary; arbitrary debug fields never enter actor input.
    static void noteEvent(const Npc& actor, const Npc* other, std::string_view event, uint64_t gameMs);

    // The packet is valid JSON even when empty. Rows share one generation-wide sequence;
    // dropped counters account for bounded-queue losses. Draining never advances game time.
    static std::string drainJson();
    static std::string statsJson();
  };
