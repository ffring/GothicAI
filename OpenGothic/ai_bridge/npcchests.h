#pragma once

#include <string>
#include <string_view>

class World;
class Npc;
class Interactive;

// An npc's chests (v28, docs/npc-tools.md «Сундуки»): which the npc may call its own, what lies in them, and
// the chest by its id for `use_chest`. The world's own data names an owner for few chests (Lobart, Lee,
// Rukhar): a chest is also the npc's when it carries its key, or when nobody owns it and it stands by a
// point of the npc's daily routine (Brago's in the bandits' cave).
namespace NpcChests {
  // a chest's id: its name in the world (CHEST_LOBART), else where it stands (MOB_17950_3240_-21850)
  std::string  id(Interactive& chest);
  Interactive* find(World& world, std::string_view id);
  // why the npc may call the chest its own: "owner", "key", "home"; empty: it is not its
  std::string  whose(World& world, Npc& npc, Interactive& chest);
  // the npc's chests, nearest first: a JSON array of {id, why, pos, distance, waypoint, locked, key,
  // has_key, items[{item, name, count}], start[…]: what the world's data put in it at the start}
  std::string  of(World& world, Npc& npc);
  // what lies in the chest: a JSON array of {item, name, count}
  std::string  items(World& world, Interactive& chest);
  }
