#pragma once

#include <string>
#include <vector>
#include "knownroads.h"

class World;
class Npc;

// Stand commands only; roads neither move NPCs nor alter their daily routines.
namespace KnownRoadsBench {
  // Read the walking actor's enabled knowledge; never grants additional roads.
  KnownRoads::Path readPath(World& world, const Npc& npc, const Tempest::Vec3& goal,
                            Tempest::Vec3& next);
  void reset();
  void tick(World* world);
  std::string command(const std::vector<std::string>& args, World& world,
                      const std::string& outputDirectory);
}
