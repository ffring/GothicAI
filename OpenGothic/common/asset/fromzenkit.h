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
