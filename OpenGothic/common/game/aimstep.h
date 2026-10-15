#pragma once

#include <cmath>

// One approach per drawn, downward look. Player input keeps control until the look or draw is reset.
class AimStep final {
  public:
    static constexpr float SearchRange = 120.f;

    struct Input final {
      bool  drawn = false;
      bool  moving = false;
      bool  edge = false;
      bool  blocked = false;
      float down = 0;       // degrees below the horizon
      float yaw = 0;        // degrees, world facing
      float distance = 0;   // cm to a drop greater than 100 cm
      };

    bool update(const Input& in) {
      if(!in.drawn || in.down<=40.f) {
        *this = AimStep();
        return false;
        }
      if(in.moving || (active && std::abs(std::remainder(in.yaw-startYaw,360.f))>30.f)) {
        active = false;
        cancelled = true;
        }
      if(cancelled)
        return false;
      if(active && (!in.edge || in.distance>SearchRange || in.distance<=35.f || in.blocked)) {
        active = false;
        cancelled = true;
        }
      if(!active && !cancelled && in.edge && in.distance>35.f && in.distance<=SearchRange && !in.blocked) {
        active = true;
        startYaw = in.yaw;
        }
      return active;
      }

    bool isActive() const { return active; }

  private:
    bool  active = false;
    bool  cancelled = false;
    float startYaw = 0;
  };
