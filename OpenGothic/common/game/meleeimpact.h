#pragma once

#include <Tempest/Vec>
#include <cstdint>

namespace MeleeImpact {
  enum class Weapon : uint8_t { Fist, OneHand, TwoHand };

  // World contact velocity is in cm/s; the returned impulse is in N s.
  Tempest::Vec3 impulse(Weapon weapon, const Tempest::Vec3& velocityCmS, float surfaceAngleDeg);

  struct Posture final {
    float value = 0;
    uint64_t lastHitMs = 0;
    uint64_t immuneUntilMs = 0;

    void tick(uint64_t nowMs, uint64_t dtMs);
    bool hit(float impulseNs, float bodyMassKg, uint64_t nowMs);
    void recover(uint64_t nowMs);
    void reset();
    };
  }
