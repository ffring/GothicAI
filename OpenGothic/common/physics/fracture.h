#pragma once

#include "physics/physics.h"
#include <Tempest/Vec>
#include <memory>
#include <string>
#include <string_view>
#include <vector>

// Offline cache geometry in source-visual centimetres. Piece transforms use
// these coordinates directly, so texture coordinates remain unchanged.
struct FractureMesh {
  struct Vertex {
    Tempest::Vec3 position, normal;
    Tempest::Vec2 uv;
    };
  struct Triangle {
    std::string texture;
    Vertex vertices[3];
    };
  struct Piece {
    double volume = 0; // cubic centimetres
    Tempest::Vec3 center;
    std::vector<Triangle> triangles;
    JPH::ShapeRefC hull;
    };
  std::vector<Piece> pieces;
  double volume = 0;

  // OPENGOTHIC_FRACTURE, otherwise ~/.local/share/gothic-ai/fracture.
  static std::shared_ptr<const FractureMesh> load(std::string_view visual);
  };

struct FractureRule {
  std::string visualPattern, className;
  float health = 100;
  float threshold = 6; // newton seconds per kilogram (delta velocity, m/s)
  float impactDamage = 50; // health / (Ns/kg) above threshold
  float arrowDamage = 8.333334f; // health / Ns; 0.12 kg arrow at 30 m/s => 30 health
  static const FractureRule* classify(std::string_view visual);
  };
