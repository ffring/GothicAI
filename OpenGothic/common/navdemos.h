#pragma once

#include <cstdint>
#include <string>
#include <vector>

class World;
class Npc;
namespace NavDemos {
void reset();
bool running();
void command(const std::vector<std::string>& args, World& world, Npc& player,
             const std::string& output);
void tick(World& world, uint64_t dt);
}
