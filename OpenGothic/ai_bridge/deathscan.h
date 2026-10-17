#pragma once

#include <unordered_set>
#include <vector>

#include "world/objects/npc.h"
#include "world/world.h"

// Deaths in the world since the last look, found by going over its npc's: the director's news (npc_died,
// storyworld.h) and an event of the triggers (storytriggers.h). The dead of a world that was just loaded are
// not news: the first look only takes them in.
class DeathScan final {
  public:
    struct Death final {
      Npc* npc    = nullptr;
      Npc* killer = nullptr;  // whose hit was the last, if anybody's
      };

    std::vector<Death> look(World& world) {
      std::vector<Death> ret;
      for(uint32_t i=0; i<world.npcCount(); ++i) {
        Npc* n = world.npcById(i);
        if(n==nullptr || !n->isDead() || n->isPlayer() || !dead.insert(n).second || !primed)
          continue;
        ret.push_back(Death{n,n->lastHitBy()});
        }
      primed = true;
      return ret;
      }

    // a load or a new world: its dead are not news
    void clear() {
      dead.clear();
      primed = false;
      }

  private:
    std::unordered_set<const Npc*> dead;    // deaths already known
    bool                           primed = false;
  };
