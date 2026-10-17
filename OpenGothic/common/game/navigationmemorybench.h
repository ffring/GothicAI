#pragma once

#include <cstdint>
#include <string>
#include <vector>

class Npc;
class World;
class PlayerControl;

// Runner-only owner. The memory itself only receives eye samples and body poses.
namespace NavigationMemoryBench {
  void reset(bool keepJournal = false);
  std::string command(const std::vector<std::string>& args, World& world, Npc& player,
                      PlayerControl& control, const std::string& outputDirectory);
  void tick(World& world);
}
