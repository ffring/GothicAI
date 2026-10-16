#ifndef NAVMESH_H
#define NAVMESH_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

/** Bench-only Recast/Detour mesh. All coordinates and dimensions are metres, Y up.
 * Nothing in the normal world load or NPC movement constructs this object. */
class NavMesh final {
  public:
    using Point = std::array<float,3>;
    struct Geometry {
      std::vector<float> vertices;
      std::vector<int>   triangles;
      std::vector<float> waterVertices;
      std::vector<int>   waterTriangles;
      };
    struct Bounds {
      Point min = {}, max = {};
      };
    enum class LinkType : uint8_t { Jump=1, Climb=2, Drop=3 };
    // A link is supplied only after a native NPC movement probe has succeeded.
    // Reverse traversal needs its own probe, or an explicitly verified bidirectional link.
    struct OffMeshLink {
      Point start = {}, end = {};
      float radius = 0.6f;
      LinkType type = LinkType::Jump;
      bool bidirectional = false;
      uint32_t id = 0;
      };
    struct Params {
      float radius = 0.2f;
      float height = 0.9f;
      float climb = 0.5f;
      float slope = 45.f;
      float cellSize = 0.2f;
      float cellHeight = 0.1f;
      float waterDepth = 0.5f;
      int   tileSize = 128;
      // Empty means the complete world; optional regions support bounded builds.
      std::vector<Bounds> regions;
      float maxBuildSeconds = 600.f;
      float nearestHorizontal = 1.f;
      float nearestVertical = 2.f;
      std::vector<OffMeshLink> links;
      };
    struct Stats {
      std::string key;
      std::filesystem::path cacheFile;
      bool fromCache = false;
      bool complete = false;
      double buildSeconds = 0;
      double loadSeconds = 0;
      uint64_t cacheBytes = 0;
      uint64_t triangles = 0;
      uint64_t waterTriangles = 0;
      uint64_t tiles = 0;
      uint64_t polygons = 0;
      uint64_t tilesConsidered = 0;
      };
    struct Path {
      struct Transition {
        Point start = {}, end = {};
        LinkType type = LinkType::Jump;
        uint32_t id = 0;
        // Both endpoints are explicit corners: start at this index, end at index+1.
        size_t cornerIndex = 0;
        };
      bool complete = false;
      std::string reason;
      std::vector<Point> corners;
      std::vector<Transition> transitions;
      float length = 0;
      float startSnapDistance = 0;
      float endSnapDistance = 0;
      };
    struct GapCandidate {
      Point start = {}, end = {};
      float horizontalDistance = 0;
      float verticalDistance = 0;
      };

    NavMesh();
    ~NavMesh();
    NavMesh(const NavMesh&) = delete;
    NavMesh& operator=(const NavMesh&) = delete;
    NavMesh(NavMesh&&) noexcept;
    NavMesh& operator=(NavMesh&&) noexcept;

    void buildOrLoad(const Geometry& geometry, const Params& params,
                     const std::filesystem::path& cacheDirectory, const std::string& worldName);
    Path findPath(const Point& start, const Point& end) const;
    // Nearest opposing boundaries of the two disconnected ground components.
    // Candidates are geometric probe sites, never permission to add a link.
    std::vector<GapCandidate> gapCandidates(const Point& start, const Point& end,
                                          float maxHorizontal=4.f, float maxVertical=3.f,
                                          size_t maxCandidates=8) const;
    void writePolygons(const std::filesystem::path& output) const;
    const Stats& stats() const;
    const Params& params() const;

  private:
    struct Impl;
    std::unique_ptr<Impl> impl;
  };

#endif
