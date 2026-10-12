#pragma once

#include <cctype>
#include <string>
#include <string_view>

#include "game/gamescript.h"
#include "world/objects/npc.h"
#include "world/world.h"

namespace AiNpc {

inline std::string upper(std::string_view s) {
  std::string r(s);
  for(auto& c:r)
    c = char(std::toupper(static_cast<unsigned char>(c)));
  return r;
  }

// An npc by script symbol ("PLAYER" for the hero). Symbols of monsters are shared by all of their kind
// ("WOLF"): with `near` the closest living one is taken, not just the first in the world.
inline Npc* findNpc(World& world, std::string_view name, const Npc* near = nullptr) {
  if(name=="PLAYER" || name=="HERO")
    return world.player();
  const size_t id = world.script().findSymbolIndex(upper(name));
  if(id==size_t(-1))
    return nullptr;
  if(near!=nullptr) {
    Npc*  best  = nullptr;
    float bestD = 0;
    world.detectNpc(near->position(),5000,[&](Npc& n) {
      if(n.instanceSymbol()!=id || n.isDead() || &n==near)
        return;
      const float d = n.qDistTo(*near);
      if(best==nullptr || d<bestD) {
        best  = &n;
        bestD = d;
        }
      });
    if(best!=nullptr)
      return best;
    }
  return world.findNpcByInstance(id);
  }

// a script state function (ZS_ATTACK, ZS_FLEE...), invalid if the scripts have none by that name
inline ScriptFn scriptState(World& world, std::string_view name) {
  const size_t id = world.script().findSymbolIndex(upper(name));
  return id==size_t(-1) ? ScriptFn() : ScriptFn(id);
  }

}
