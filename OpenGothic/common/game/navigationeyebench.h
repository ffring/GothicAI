#pragma once

#include <cstdint>
#include <string>
#include <vector>

class Npc;
class World;

// Runner-only navigation-eye fixtures. Kept separate from the navigation mesh
// and bridge commands so both experiments can evolve independently.
namespace NavigationEyeBench {
  void reset();
  std::string command(const std::vector<std::string>& args, World& world, Npc& player,
                      const std::string& outputDirectory);
  void tick(uint64_t gameMs);
}
