#pragma once

#include <cstddef>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include "asset/worldmesh.h"
#include "worlddata.h"

struct WorldBaseResult {
  bool        replaced = false;
  size_t      triangles = 0;
  size_t      chunks = 0;
  double      readMs = 0;
  std::string error;
  };

// Select a complete world without opening its ZEN. Failure leaves outputs untouched.
WorldBaseResult tryWorldData(zenkit::World& world, asset::WorldMesh& worldMesh, std::vector<WorldData::Room>& rooms,
                            std::string_view name, const std::filesystem::path& dir,
                            const std::filesystem::path& overrideDir,
                            const std::vector<std::pair<std::string,uintmax_t>>& mounted);
