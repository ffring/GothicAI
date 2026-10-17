#pragma once

#include <cstdint>
#include <deque>

// Engine-independent progress window. Times are simulation milliseconds, never wall time.
class StuckProgress final {
  public:
    static constexpr uint64_t WindowMs = 3000;
    static constexpr float    ProgressCm = 50.f;
    static constexpr float    RecoveryCm = 100.f;

    struct Point { float x=0, y=0, z=0; };
    enum Change { None, Detected, Recovered };

    Change update(uint64_t now, Point point, int64_t gameTime) {
      if(active) {
        if(distanceSquared(point,origin)>RecoveryCm*RecoveryCm) {
          active = false;
          history.clear();
          history.push_back({now,point,gameTime});
          return Recovered;
          }
        return None;
        }
      history.push_back({now,point,gameTime});
      if(now<history.front().time || now-history.front().time<WindowMs)
        return None;
      const auto cutoff = now-WindowMs;
      while(history.size()>1 && history[1].time<=cutoff)
        history.pop_front();
      Point from = history.front().point;
      int64_t fromGameTime = history.front().gameTime;
      if(history.size()>1 && history[1].time>history[0].time) {
        const auto& a = history[0];
        const auto& b = history[1];
        const float t = float(cutoff-a.time)/float(b.time-a.time);
        from = {a.point.x+(b.point.x-a.point.x)*t,
                a.point.y+(b.point.y-a.point.y)*t,
                a.point.z+(b.point.z-a.point.z)*t};
        fromGameTime += int64_t(double(b.gameTime-a.gameTime)*double(t));
        }
      if(distanceSquared(point,from)>=ProgressCm*ProgressCm)
        return None;
      active = true;
      origin = from;
      startedAt = cutoff;
      startedGameTime = fromGameTime;
      detectedAt = now;
      history.clear();
      return Detected;
      }

    bool isStuck() const { return active; }
    Point position() const { return origin; }
    uint64_t start() const { return startedAt; }
    uint64_t detected() const { return detectedAt; }
    int64_t gameTime() const { return startedGameTime; }

  private:
    static float distanceSquared(Point a, Point b) {
      return (a.x-b.x)*(a.x-b.x)+(a.y-b.y)*(a.y-b.y)+(a.z-b.z)*(a.z-b.z);
      }
    struct Sample { uint64_t time; Point point; int64_t gameTime; };
    std::deque<Sample> history;
    bool active = false;
    Point origin;
    uint64_t startedAt = 0, detectedAt = 0;
    int64_t startedGameTime = 0;
  };
