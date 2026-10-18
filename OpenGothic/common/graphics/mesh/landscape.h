#pragma once

#include <Tempest/Device>
#include <Tempest/Matrix4x4>
#include <Tempest/UniformBuffer>

#include "graphics/visualobjects.h"
#include "graphics/mesh/submesh/staticmesh.h"

class PackedMesh;

class Landscape final {
  public:
    Landscape(VisualObjects& visual, const PackedMesh& wmesh);

    void setVisible(bool visible);

    const Tempest::StorageBuffer& bvh()   const { return bvhNodes; }

  private:
    using Item = VisualObjects::Item;

    struct Block {
      Item mesh;
      };

    std::vector<Block>     blocks;
    StaticMesh             mesh;
    Tempest::StorageBuffer meshletDesc;

    Tempest::StorageBuffer bvhNodes;
  };
