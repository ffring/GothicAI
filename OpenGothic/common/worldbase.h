#pragma once

#include <cstddef>
#include <filesystem>
#include <string>
#include <string_view>
#include <vector>

#include <zenkit/Mesh.hh>

namespace zenkit { class Read; }

struct WorldBaseResult {
  bool        replaced = false;
  size_t      triangles = 0;
  size_t      chunks = 0;
  double      readMs = 0;
  std::string error;
  };

std::vector<std::byte> readWorldBytes(zenkit::Read& reader);

// A missing or disabled base leaves the mesh unchanged with no error.
// Source mismatches and read failures leave it unchanged with an error.
WorldBaseResult tryWorldBase(zenkit::Mesh& mesh, std::string_view world,
                             const std::vector<std::byte>& source,
                             const std::filesystem::path& dir,
                             const std::filesystem::path& overrideDir = {});
