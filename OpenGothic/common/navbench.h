#pragma once
#include <string>
#include <vector>
class World;
class Npc;
class NavMesh;
namespace NavBench {
const NavMesh& navigationMesh(World& world);
void reset();
bool running();
void command(const std::vector<std::string>& args, World& world, Npc& player, const std::string& output);
void tick(World& world);
}
