#pragma once

#include <filesystem>
#include <cstdint>
#include <vector>
#include <zenkit/MultiResolutionMesh.hh>

// Loads the supported static, embedded-buffer glTF 2.0 subset into Gothic
// centimetres. No resources, graphics device or archive access is involved.
// Throws std::runtime_error with the rejection reason on invalid input.
// Optionally returns the exact GLB bytes used by the successful conversion.
zenkit::MultiResolutionMesh loadGltfMesh(const std::filesystem::path& path, std::vector<uint8_t>* source = nullptr);
