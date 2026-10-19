#pragma once

#include "navdemowriter.h"

#include <array>
#include <cstdint>
#include <vector>

// A keyboard teacher whose entire observation is a subset of NAVDEMO v2's
// learner_input. No world pose, object identity, world query or NavMesh enters
// this interface. Memory is quantized BEFORE planning, exactly as on disk.
class NavMemoryTeacher final {
  public:
    struct Cell final {
      // confidence, free, wall, ledge, drop, water, movable (NAVDEMO order).
      std::array<uint8_t,7> channels = {};
      float groundHeightCm = 0;
      uint8_t heightValid = 0;
      };
    struct Input final {
      NavDemoWriter::Body body;
      NavDemoWriter::Roads roads;
      NpcKeys::KeyState previous;
      std::array<Cell,64*64> near = {};
      std::array<Cell,100*100> far = {};

      // Reads ONLY the layers of memory, never memory.pose or traveledCm.
      static Input fromLearner(const NavDemoWriter::Body& body,
                               const NavDemoWriter::Roads& roads,
                               const NpcKeys::KeyState& previous,
                               const NavigationMemory::View& memory);
      };
    struct Point final {
      // Current body frame, centimetres; positive right / forward.
      float rightCm = 0, forwardCm = 0;
      };

    void reset();
    // Call exactly once per 100-ms decision, reset at every episode boundary.
    // Airborne observations never request jumps or climbing.
    NpcKeys::KeyState decide(const Input& input);
    // Latest full-resolution memory route for debug/rendering ONLY. Starts at
    // the body origin; its points have no world coordinates or object IDs.
    const std::vector<Point>& plan() const { return route; }
    float planLengthCm() const { return routeLength; }
    bool planComplete() const { return complete; }

  private:
    std::vector<Point> route;
    float routeLength = 0;
    uint32_t stuckTicks = 0, recoveryTicks = 0, recoveryAttempts = 0;
    float recoverySide = 1;
    bool complete = false;
    bool observedGoal = false;
    Point previousGoal;
    uint32_t movingTicks = 0;
  };
