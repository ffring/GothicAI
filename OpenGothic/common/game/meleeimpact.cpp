#include "meleeimpact.h"

#include <algorithm>
#include <cmath>

Tempest::Vec3 MeleeImpact::impulse(Weapon weapon, const Tempest::Vec3& velocityCmS, float surfaceAngleDeg) {
  const float mass = weapon==Weapon::Fist ? 1.5f : weapon==Weapon::OneHand ? 2.5f : 4.f;
  const float edge = .5f+.5f*std::sin(std::clamp(surfaceAngleDeg,0.f,90.f)*float(M_PI)/180.f);
  auto result = velocityCmS*(mass*.01f*edge);
  const float magnitude = result.length();
  if(magnitude>60.f)
    result *= 60.f/magnitude;
  return result;
  }

void MeleeImpact::Posture::tick(uint64_t nowMs, uint64_t dtMs) {
  if(nowMs<=lastHitMs+1500)
    return;
  const auto elapsed = std::min(dtMs,nowMs-lastHitMs-1500);
  value = std::max(0.f,value-float(elapsed)*.03f);
  }

bool MeleeImpact::Posture::hit(float impulseNs, float bodyMassKg, uint64_t nowMs) {
  const float threshold = 120.f*bodyMassKg/75.f;
  value = std::min(nowMs<immuneUntilMs ? 60.f : 100.f,value+100.f*impulseNs/threshold);
  lastHitMs = nowMs;
  return value>=100.f;
  }

void MeleeImpact::Posture::recover(uint64_t nowMs) {
  value = 0;
  lastHitMs = nowMs;
  immuneUntilMs = nowMs+4000;
  }

void MeleeImpact::Posture::reset() {
  *this = Posture();
  }
