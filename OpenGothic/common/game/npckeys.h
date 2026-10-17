#pragma once

#include <cstdint>
#include <Tempest/Vec>

class Npc;

// Digital locomotion input, independent of the actor's AI/body ownership. The optional stand
// registry below is the only owner of held keys; applyKeys does not consult it.
class NpcKeys final {
  public:
    struct KeyState final {
      float forward = 0; // positive: forward; negative: back
      float strafe  = 0; // positive: right; negative: left
      float turn    = 0; // positive: right; negative: left (90 degrees/s unarmed)
      bool  walk    = false;
      bool  jump    = false;
      bool  climb   = false; // forward + jump at an edge, with the player's collision tests
      float headYaw   = 0; // degrees relative to the body, positive: left
      float headPitch = 0; // degrees, positive: up
      };

    // One actor tick: advance its existing movement, then apply input, in the same phase order
    // as Npc::tick followed by PlayerControl::tickMove. No registry, lease or AI state is owned.
    static void applyKeys(Npc& npc, const KeyState& keys, uint64_t dt);

    // Stand-only held input. TTL uses World::tickCount, never wall time. Expiration releases
    // every key while keeping the override enabled; off returns the suspended native AI.
    static void enable(Npc& npc, bool enabled);
    static bool set(Npc& npc, const KeyState& keys, uint64_t ttlMs);
    static bool enabled(const Npc& npc);
    static bool tick(Npc& npc, uint64_t dt);
    static void forget(const Npc* npc);
    static void reset();

    struct Status final {
      bool enabled = false;
      bool hasCommand = false;
      KeyState keys;
      uint64_t remainingMs = 0;
      };
    static Status status(const Npc& npc);

    // Read-only stand measurement, separate from the model's presentation transform.
    static Tempest::Vec3 physicalPosition(const Npc& npc);

    struct Stats final {
      uint64_t ticks = 0, commands = 0, expired = 0;
      uint64_t totalNanoseconds = 0, maxNanoseconds = 0;
      uint64_t partialPeerTicks = 0, peerChecks = 0;
      };
    static void setProfiling(bool enabled);
    static void resetStats();
    static Stats stats();
  };
