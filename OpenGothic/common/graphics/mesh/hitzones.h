#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

class Skeleton;

// HitZones: where an arrow hits a body - the head, the trunk, a limb. Capsules along the bones, made from
// the skeleton's bind pose by the bone names (a human, a wolf, a troll alike), tested against the animated
// bones: an arrow that passes between the arm and the trunk misses
class HitZones final {
  public:
    enum Zone : uint8_t {
      None,
      Body,
      Head,
      Limb,
      };

    void             setSkeleton(const Skeleton* sk);
    bool             isValid() const { return !caps.empty(); }

    // The first zone the segment from-to enters; t: how far from 'from'. tr: the world-space bones,
    // scale: of the npc (the radii grow with it)
    Zone             rayTest(const Tempest::Matrix4x4* tr, size_t numBones, float scale,
                             const Tempest::Vec3& from, const Tempest::Vec3& to, float& t) const;
    // The middle of a zone: the test bench aims there
    bool             center(const Tempest::Matrix4x4* tr, size_t numBones, Zone z, Tempest::Vec3& at) const;

    struct Posed final {
      Tempest::Vec3 a, b;
      float         r    = 0;
      Zone          zone = None;
      };
    // the capsules as posed (the test bench draws them)
    void             posed(const Tempest::Matrix4x4* tr, size_t numBones, float scale, std::vector<Posed>& out) const;

    static std::string_view name(Zone z);
    // the capsules as posed, for debugging: (a, b, radius, zone)
    std::string      dump(const Tempest::Matrix4x4* tr, size_t numBones, float scale) const;

  private:
    struct End final {
      size_t        bone = 0;
      Tempest::Vec3 at   = {}; // in the bone's frame
      };
    struct Capsule final {
      End   a, b;
      float r    = 0;
      Zone  zone = None;
      };

    std::vector<Capsule> caps;

    static Tempest::Vec3 point(const Tempest::Matrix4x4* tr, const End& e);
  };
