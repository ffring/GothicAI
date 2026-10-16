#pragma once

#include "ikmath.h"

// A frame for manual aiming near the vertical poles. The body supplies the missing heading;
// away from the poles the original world-up frame is retained exactly.
namespace AimFrame {

inline float poleWeight(const Tempest::Vec3& axis) {
  constexpr float Cone = 0.34202014f; // sin(20 degrees): unchanged through 70 degrees up/down
  const float h = std::sqrt(axis.x*axis.x + axis.z*axis.z);
  const float t = std::clamp(h/Cone,0.f,1.f);
  const float s = t*t*(3.f-2.f*t);
  return 1.f-s*s;
  }

inline Tempest::Vec3 up(const Tempest::Vec3& axis, const Tempest::Vec3& forward) {
  using namespace IkMath;
  const float k = poleWeight(axis);
  const Vec3 vertical = Up-axis*axis.y;
  const Vec3 facing = forward-axis*dot(forward,axis);
  // Straight down: the upper limb points away from the body. Projecting both supports
  // keeps the result perpendicular to the arrow, including at and across the pole.
  return vertical*(1.f-k) - facing*(axis.y*k);
  }

}
