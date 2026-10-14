#pragma once

#include <Jolt/Jolt.h>
#include <Jolt/Physics/Collision/Shape/Shape.h>

namespace Physics {
  // Shapes can be loaded by Resources before the first world is constructed.
  void initialize();
  uint32_t triangleData(const JPH::Shape& shape, JPH::SubShapeID subShape);
  }
