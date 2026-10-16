#pragma once
#include <string>
#include <vector>
class World;
class Npc;
namespace NavBench {
void reset();
bool running();
void command(const std::vector<std::string>& args, World& world, Npc& player, const std::string& output);
void tick(World& world);
}
