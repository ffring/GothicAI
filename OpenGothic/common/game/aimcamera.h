#pragma once

#include <algorithm>

// Move the manual camera's sight line in front of the hands for steep downward shots.
// The ordinary shoulder position is exact through 30 degrees, including upward aim.
namespace AimCamera {

inline float advance(float pitch) {
  const float k = std::clamp((pitch-30.f)/30.f,0.f,1.f);
  return 60.f*k*k*(3.f-2.f*k);
  }

inline float follow(float current, float pitch, float dt) {
  const float wanted = advance(pitch);
  // At 30 fps the correction travels at most 6 cm, leaving room for the mouse's orbit.
  const float step = 180.f*std::clamp(dt,0.f,0.05f);
  return current + std::clamp(wanted-current,-step,step);
  }

}
