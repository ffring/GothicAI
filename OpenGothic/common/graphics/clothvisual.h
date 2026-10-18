#pragma once

#include <Tempest/Matrix4x4>
#include <zenkit/vobs/VirtualObject.hh>
#include <memory>
#include <string_view>

class World;
class Cloth;
class MeshObjects;

#include "graphics/meshobjects.h"

// Owns the transient render/physics binding of an authored cloth mesh-vob.
// The ordinary mesh and its morph/wind animation remain the distant fallback.
class ClothVisual final {
  public:
    static bool supports(std::string_view visual);
    static std::unique_ptr<ClothVisual> create(MeshObjects::Mesh& view, World& world,
                                               const zenkit::VirtualObject& vob, bool enableCollision);
    ~ClothVisual();
    void setView(MeshObjects::Mesh& view);
    void setMatrix(const Tempest::Matrix4x4& matrix);

  private:
    struct State;
    ClothVisual();
    std::shared_ptr<State> state;
  };
