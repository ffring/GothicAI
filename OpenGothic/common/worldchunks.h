#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include <zenkit/Mesh.hh>

// The converted world mesh: one .glb per 64 x 64 m chunk plus world.json.
// Format: docs/asset-migration.md#мир. Only the triangle set the engine uses
// is carried (zenkit::Mesh::polygons after the BSP filter): materials,
// vertices, features and polygons. BSP, sectors, portals and raw polygons
// still come from the .ZEN.

struct WorldChunkSource {
  std::string world;       // archive name, e.g. NEWWORLD.ZEN
  uint64_t    size  = 0;   // bytes of the .ZEN the chunks were cut from
  uint32_t    crc32 = 0;
  };

struct WorldChunkManifest {
  WorldChunkSource         source;
  int32_t                  chunkSize = 6400; // centimetres
  std::vector<std::string> chunks;           // file names, relative to the world directory
  uint64_t                 triangles = 0;
  uint64_t                 vertices  = 0;    // size of the source arrays
  uint64_t                 features  = 0;
  uint64_t                 unreferencedVertices = 0;
  uint64_t                 unreferencedFeatures = 0;
  std::vector<zenkit::Material> materials;  // the full source table
  };

// Writes world.json and chunks/<x>_<z>.glb into dir (created if needed).
// Throws std::runtime_error on invalid input or I/O failure.
WorldChunkManifest writeWorldChunks(const zenkit::Mesh& mesh, const WorldChunkSource& source,
                                    const std::filesystem::path& dir, int32_t chunkSize = 6400);

WorldChunkManifest readWorldChunkManifest(const std::filesystem::path& dir);

// Reads the chunks listed in dir/world.json. A chunk with the same file name
// under overrideDir (if not empty) replaces the base one. Fills materials,
// vertices, features and polygons of a fresh mesh. Unedited chunks restore
// the source arrays exactly, with every source index (the engine's meshlet
// packing keys on them); geometry an edit added or moved goes after them.
zenkit::Mesh readWorldChunks(const std::filesystem::path& dir, const std::filesystem::path& overrideDir = {});

// The fields readWorldChunks fills, copied from a source mesh: materials,
// vertices, features and polygons. readWorldChunks of unedited chunks equals
// canonicalWorldMesh of the source mesh bit for bit.
zenkit::Mesh canonicalWorldMesh(const zenkit::Mesh& mesh);

// Empty when equal; otherwise the first difference, for logs and tests.
std::string compareWorldMeshes(const zenkit::Mesh& expected, const zenkit::Mesh& actual);

uint32_t worldChunkCrc32(const void* data, size_t size, uint32_t crc = 0);
