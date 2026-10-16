#pragma once

#include <string>
#include <vector>

class World;

// Stand commands only; roads neither move NPCs nor alter their daily routines.
namespace KnownRoadsBench {
  void reset();
  void tick(World* world);
  std::string command(const std::vector<std::string>& args, World& world,
                      const std::string& outputDirectory);
}
