#pragma once

#include <Tempest/Vec>

#include <cmath>
#include <optional>

// Value-only math for optional near-wall reset placement. Native collision queries and
// capsule/visibility/path validation remain in BodyArena; this never changes live actors.
namespace BodyArenaGeometry {
inline bool validTangentSelection(std::optional<int> requested, bool nearWall, bool hasClearance) {
  return !requested || (nearWall && hasClearance && (*requested==1 || *requested==-1));
  }

inline bool acceptsTangentSign(std::optional<int> requested, int candidate) {
  return !requested || *requested==candidate;
  }

struct WallBasis final {
  Tempest::Vec3 outward;
  Tempest::Vec3 toward;
  Tempest::Vec3 tangent;
  };

inline std::optional<WallBasis> horizontalBasis(const Tempest::Vec3& normal,
                                               const Tempest::Vec3& searchToward) {
  using Tempest::Vec3;
  if(!std::isfinite(normal.x) || !std::isfinite(normal.y) || !std::isfinite(normal.z) ||
     !std::isfinite(searchToward.x) || !std::isfinite(searchToward.y) || !std::isfinite(searchToward.z))
    return {};
  Vec3 outward(normal.x,0,normal.z);
  const auto length=outward.length();
  if(!std::isfinite(length) || length<0.000001f) return {};
  outward/=length;
  const auto orientation=Vec3::dotProduct(outward,searchToward);
  if(!std::isfinite(orientation) || std::abs(orientation)<0.000001f) return {};
  if(orientation>0) outward=-outward;
  const auto toward=-outward;
  return WallBasis{outward,toward,Vec3(-toward.z,0,toward.x)};
  }

inline float normalClearance(const Tempest::Vec3& origin, const Tempest::Vec3& wallPoint,
                             const Tempest::Vec3& outward) {
  return Tempest::Vec3::dotProduct(origin-wallPoint,outward);
  }

inline bool acceptsClearance(float requested, float rayClearance, float normalClearance) {
  return std::isfinite(requested) && std::isfinite(rayClearance) && std::isfinite(normalClearance) &&
         requested>=60.f && requested<=180.f && rayClearance>=60.f && rayClearance<=180.f &&
         normalClearance>=60.f && normalClearance<=180.f &&
         std::abs(rayClearance-requested)<=20.f && std::abs(normalClearance-requested)<=20.f;
  }
}
