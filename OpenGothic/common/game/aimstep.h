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
      bool  shotClear = false; // leanNeeded found a safe pose whose shot passes the nearby edge
      float down = 0;       // degrees below the horizon
      float yaw = 0;        // degrees, world facing
      float distance = 0;   // cm to a drop greater than 100 cm
      };

    bool update(const Input& in) {
      if(!in.drawn || in.down<=40.f) {
        *this = AimStep();
        return false;
        }
      if(in.moving || (engaged && std::abs(std::remainder(in.yaw-startYaw,360.f))>30.f)) {
        active = false;
        cancelled = true;
        }
      if(cancelled)
        return false;
      if(engaged && (!in.edge || in.distance>SearchRange || in.blocked)) {
        active = false;
        cancelled = true;
        }
      if(!engaged && !cancelled && in.edge && in.distance>35.f && in.distance<=SearchRange &&
         !in.blocked && !in.shotClear) {
        engaged = true;
        startYaw = in.yaw;
        }
      // Stay in this approach while the pose settles: if a newly sampled pose closes the shot again,
      // take the next step even inside 35 cm, until the movement guard holds the body.
      active = engaged && !cancelled && !in.shotClear;
      return active;
      }

    bool isActive() const { return active; }

  private:
    bool  active = false;
    bool  engaged = false;
    bool  cancelled = false;
    float startYaw = 0;
  };
