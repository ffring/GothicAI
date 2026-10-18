#pragma once

#include <zenkit/Mesh.hh>

#include <cstdint>
#include <string>
#include <vector>

// Authored cloth polygons are split before either landscape packing pass.
// This keeps the original triangles available for the cloth=0 fallback without
// retaining a second copy of the entire landscape or changing BSP sectors.
namespace WorldCloth {

struct Surface {
  zenkit::Mesh mesh;
  std::vector<uint32_t> pins;
  std::string visual;
  uint32_t id = 0;
  float stiffness = 0.85f;
  };

struct Extracted {
  zenkit::Mesh original;
  std::vector<Surface> surfaces;
  };

Extracted extract(zenkit::Mesh& landscape);

}
