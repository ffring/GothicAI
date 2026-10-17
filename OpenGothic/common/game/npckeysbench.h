#pragma once

#include <cstdint>
#include <string>
#include <vector>

class Npc;
class World;
class PlayerControl;

// Runner fixture only. No bridge commands or ownership of the production body.
namespace NpcKeysBench {
  void reset();
  std::string command(const std::vector<std::string>& args, World& world, Npc& player,
                      PlayerControl& control, const std::string& outputDirectory);
  void tick(World& world, Npc& player, PlayerControl& control, uint64_t dt);
}
