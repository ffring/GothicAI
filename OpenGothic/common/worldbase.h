#pragma once

#include <cstddef>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include "asset/worldmesh.h"
#include "worlddata.h"

namespace zenkit { class Read; }

struct WorldBaseResult {
  bool        replaced = false;
  size_t      triangles = 0;
  size_t      chunks = 0;
  double      readMs = 0;
  std::string error;
  };

std::vector<std::byte> readWorldBytes(zenkit::Read& reader);

// Select a complete world without opening its ZEN. Failure leaves outputs untouched.
WorldBaseResult tryWorldData(zenkit::World& world, asset::WorldMesh& worldMesh, std::vector<WorldData::Room>& rooms,
                            std::string_view name, const std::filesystem::path& dir,
                            const std::filesystem::path& overrideDir,
                            const std::vector<std::pair<std::string,uintmax_t>>& mounted);

// A missing or disabled base leaves the mesh unchanged with no error.
// Source mismatches and read failures leave it unchanged with an error.
WorldBaseResult tryWorldBase(asset::WorldMesh& mesh, std::string_view world,
                             const std::vector<std::byte>& source,
                             const std::filesystem::path& dir,
                             const std::filesystem::path& overrideDir = {});
