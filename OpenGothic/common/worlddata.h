#pragma once

#include "worldchunks.h"

#include <array>
#include <zenkit/World.hh>
#include <zenkit/vobs/Light.hh>

// Non-mesh .ZEN data, shared by the converter and the engine base loader.
// Coordinates are Gothic centimetres. See docs/asset-migration.md.
namespace WorldData {

struct Triangle { std::array<zenkit::Vec3,3> point; };
struct Room {
  zenkit::Vec3 min, max;
  std::vector<Triangle> triangles;
  };

// One volume per BSP sector, including empty rooms, in source order.
bool isIndoor(zenkit::BspTreeType mode, const std::vector<Room>& rooms, const zenkit::Vec3& point);

struct Data {
  WorldChunkSource source;
  std::string name;
  zenkit::AxisAlignedBoundingBox bbox = zenkit::AxisAlignedBoundingBox::zero();
  std::vector<std::shared_ptr<zenkit::VirtualObject>> vobs;
  std::shared_ptr<zenkit::WayNet> waynet;
  // Raw mesh polygon references are omitted, as specified for bsp.bin.
  zenkit::BspTree bsp {};
  std::vector<Room> rooms;
  };

Data fromWorld(const zenkit::World& world, const WorldChunkSource& source);
void write(const Data& data, const std::filesystem::path& dir);
Data read(const std::filesystem::path& dir, const std::filesystem::path& overrideDir = {});
// ZenKit ASCII plus vob IDs/runtime fields and ordered waypoint/edge comparison.
// Individual retained BSP fields and room floats.
// Also samples isIndoor on a grid of at most 400 cm spacing at three heights.
// Empty on equality, otherwise includes the first differing field and values.
std::string compare(const Data& expected, const Data& actual);
size_t vobCount(const std::vector<std::shared_ptr<zenkit::VirtualObject>>& vobs);

struct ZenFile {
  WorldChunkSource source;
  // "world" for meshless bundles, "cameras", or "light_presets".
  std::string kind;
  std::vector<std::shared_ptr<zenkit::VirtualObject>> vobs;
  std::shared_ptr<zenkit::WayNet> waynet;
  std::vector<zenkit::LightPreset> light_presets;
  };

// Returns false for worlds with a mesh. Unknown archive classes are errors.
bool loadZen(const std::vector<std::byte>& bytes, const WorldChunkSource& source, ZenFile& out);
void writeZen(const ZenFile& data, const std::filesystem::path& file);
ZenFile readZen(const std::filesystem::path& file);
std::string compareZen(const ZenFile& expected, const ZenFile& actual);

// Called by content-convert --self-test; writes only under the supplied scratch directory.
void selfTest(const std::filesystem::path& dir);

}
