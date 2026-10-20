#pragma once

#include <filesystem>
#include <zenkit/MultiResolutionMesh.hh>

// Loads the supported static, embedded-buffer glTF 2.0 subset into Gothic
// centimetres. No resources, graphics device or archive access is involved.
// Throws std::runtime_error with the rejection reason on invalid input.
zenkit::MultiResolutionMesh loadGltfMesh(const std::filesystem::path& path);
