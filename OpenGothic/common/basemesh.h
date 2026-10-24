#pragma once

#include <filesystem>
#include <string>

#include <zenkit/MultiResolutionMesh.hh>

// A converted static mesh (.MRM) as one .glb: docs/asset-migration.md#предметы.
// Carries what the engine reads from an MRM: positions, and per submesh its
// material, wedges (normal, UV, position index) and triangles; bbox and
// alpha_test. Progressive-mesh data (planes, edges, wedge map, colours) and
// the oriented box are not used by the engine and are not carried.

void writeBaseMesh(const zenkit::MultiResolutionMesh& mesh, const std::string& name, const std::filesystem::path& file);

zenkit::MultiResolutionMesh readBaseMesh(const std::filesystem::path& file);

// Empty when the fields above are equal bit for bit; otherwise the first difference.
std::string compareBaseMeshes(const zenkit::MultiResolutionMesh& expected, const zenkit::MultiResolutionMesh& actual);
