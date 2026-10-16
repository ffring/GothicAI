#pragma once

#include <cstdint>
#include <cstddef>
#include <string>
#include <vector>

class Npc;
class World;

// Runner-only navigation-eye fixtures. Kept separate from the navigation mesh
// and bridge commands so both experiments can evolve independently.
namespace NavigationEyeBench {
  void reset();
  // Runner fixtures can share a calibrated actor without exposing world queries.
  Npc* actor(size_t index);
  std::string command(const std::vector<std::string>& args, World& world, Npc& player,
                      const std::string& outputDirectory);
  void tick(uint64_t gameMs);
}
