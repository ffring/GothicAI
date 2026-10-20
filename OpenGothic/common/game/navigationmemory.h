#pragma once

#include "navigationeye.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <vector>

// Short visual memory. The only external information accepted here is the eye's
// observations and the actor's own body pose; no world or physics access.
class NavigationMemory final {
  public:
    static constexpr uint32_t RepresentationVersion = 2;

    struct Constants final {
      static constexpr size_t nearSize = 64, farSize = 100;
      static constexpr float nearCellCm = 25.f, farCellCm = 200.f;
      static constexpr float staticTauSeconds = 30.f, movableTauSeconds = 5.f;
      static constexpr float distanceLambdaCm = 3000.f;
      static constexpr float stepCm = 50.f;
      static constexpr float maxSlopeDegrees = 45.f, bodyHeightCm = 180.f;
      static constexpr float groundNormalY = .7f, wallNormalY = .3f;
      static constexpr float clearHeightCm = 25.f;
      };

    struct Pose final {
      Tempest::Vec3 position = {}; // feet, centimetres
      Tempest::Vec3 forward = {1.f,0.f,0.f}; // body, not head; XZ only
      uint64_t gameMs = 0;
      // The same guild limits passed to CharacterVirtual, supplied by the caller.
      float stepHeightCm = Constants::stepCm;
      float maxSlopeDegrees = Constants::maxSlopeDegrees;
      float bodyHeightCm = Constants::bodyHeightCm;
      };

    struct Cell final {
      float confidence = 0, free = 0, wall = 0, ledge = 0, drop = 0, water = 0, movable = 0;
      float groundHeightCm = std::numeric_limits<float>::quiet_NaN();
      };

    // Row-major [forward * size + right]. Cell centres in the current body frame
    // are (index + .5 - size/2) * cellCm. Unknown cells have confidence zero.
    struct Layer final {
      size_t size = 0;
      float cellCm = 0;
      std::vector<Cell> cells;
      };

    struct View final {
      Pose pose;
      double traveledCm = 0;
      Layer near, far;
      };

    NavigationMemory();
    void reset();
    // Call for every body tick, including ticks without new eye observations.
    // A stable ray order (NavigationEye::samples()) lets cached rays be skipped.
    void update(const std::vector<NavigationEye::Ray>& rays, const Pose& pose);
    // Reuses the caller's allocations. Rotation happens only at readout, never
    // in the stored world grids. Sparse observations are splatted, not blurred.
    void view(View& result) const;
    const Pose& pose() const { return body; }
    double traveledCm() const { return pathCm; }

  private:
    enum Channel : uint8_t { Free, Clearance, Ground, Wall, Water, Movable, Surface, ChannelCount };
    struct Evidence final {
      uint64_t gameMs = 0;
      float pathCm = 0, heightCm = 0;
      float hitX = 0, hitZ = 0;
      };
    struct WorldCell final {
      std::array<Evidence,ChannelCount> evidence = {};
      uint8_t known = 0;
      };
    struct Grid final {
      size_t size = 0;
      float cellCm = 0;
      int minX = 0, minZ = 0;
      bool positioned = false;
      std::vector<WorldCell> cells;

      Grid(size_t size, float cellCm);
      void center(const Tempest::Vec3& position);
      bool contains(int x, int z) const;
      WorldCell& at(int x, int z);
      const WorldCell& at(int x, int z) const;
      };
    struct RayStamp final {
      uint64_t gameMs = 0;
      bool seen = false;
      };
    struct PathStamp final { uint64_t gameMs = 0; double pathCm = 0; };

    void ingest(Grid& grid, const NavigationEye::Ray& ray, float observedPath);
    void read(const Grid& grid, Layer& result) const;
    float confidence(const Evidence& evidence, bool movable) const;
    float pathAt(uint64_t gameMs) const;

    Grid nearGrid, farGrid;
    Pose body;
    double pathCm = 0;
    bool posed = false;
    std::vector<RayStamp> rayStamps;
    // Only the interval since the oldest not-yet-consumed eye timestamp is
    // needed to assign cached observations their original travelled distance.
    std::vector<PathStamp> pathHistory;
  };
