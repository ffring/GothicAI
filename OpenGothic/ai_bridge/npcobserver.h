#pragma once

#include <rapidjson/document.h>

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "game/constants.h"

class Npc;
class World;

// NPCs that follow their routine, as seen by the AI server's planner (protocol v5):
// - npc_roster: npc's around the player, twice a second;
// - npc_event:  what they notice about the player (from the engine's own perceptions, which still run);
// - npc_act:    "soft" actions put into the npc's AI queue the way scripts do it (AI_OutputSVM,
//               AI_TurnToNpc, ...): when the queue runs empty, the engine resumes the routine itself;
// - npc_idle:   such an action is done.
// Npc's under NpcController or in a conversation are reported but never acted upon.
class NpcObserver final {
  public:
    using Emit = std::function<void(std::string&&)>;
    // "attached" / "dialog" for npc's somebody else drives, empty otherwise
    using Owner = std::function<std::string_view(std::string_view symbol)>;

    NpcObserver(Emit emit, Owner owner);

    void tick(World& world);
    void perception(World& world, Npc& self, Npc& other, Npc* victim, PercType perc);
    void act(World& world, const rapidjson::Value& msg);
    void clear();

  private:
    struct Running final {
      std::string id;
      std::string symbol;
      size_t      instance = 0;
      uint64_t    start    = 0;
      };

    void emitRoster(World& world);
    // true when the player just came close: the caller emits player_near after the roster
    bool trackProximity(World& world, Npc& npc, std::string_view symbol, float dist);
    void checkRunning(World& world, uint64_t now);
    void emitEvent(std::string_view npc, std::string_view kind, std::string_view other,
                   double distance, std::string_view detail);
    bool throttled(std::string_view npc, std::string_view kind, uint64_t now, uint64_t periodMs);
    void reply(std::string_view id, std::string_view npc, std::string_view status, std::string_view detail);

    Emit                                  emit;
    Owner                                 owner;
    uint64_t                              nextRoster = 0;
    std::unordered_map<std::string,bool>  close;      // npc -> the player is within reach
    std::unordered_map<std::string,uint64_t> lastEvent;  // "npc/kind" -> time
    std::vector<Running>                  running;
  };
