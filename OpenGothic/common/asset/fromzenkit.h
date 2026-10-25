#pragma once

#include "staticmesh.h"
#include "worldmesh.h"
#include "worlddata.h"

namespace zenkit {
class Material;
class Mesh;
class MultiResolutionMesh;
class SoftSkinMesh;
class ModelMesh;
class World;
class BspTree;
}

namespace asset {
Material fromZenKit(const zenkit::Material& source);
WorldMesh fromZenKit(const zenkit::Mesh& source);
StaticMesh fromZenKit(const zenkit::MultiResolutionMesh& source);
SoftSkinMesh fromZenKit(const zenkit::SoftSkinMesh& source);
ModelMesh fromZenKit(const zenkit::ModelMesh& source);

// Raw polygons belong to the BSP/archive boundary; the base stores these volumes.
std::vector<WorldData::Room> roomVolumesFromZenKit(const zenkit::Mesh& mesh, const zenkit::BspTree& bsp);
}

// Original archives only enter the engine through the import boundary.
namespace WorldData {
Data fromWorld(const zenkit::World& world, const WorldChunkSource& source);
// Returns false for worlds with a mesh. Unknown archive classes are errors.
bool loadZen(const std::vector<std::byte>& bytes, const WorldChunkSource& source, ZenFile& out);
// Converter contract, including all supported archive classes and graph references.
void selfTest(const std::filesystem::path& dir);
}
