#pragma once

#include <rapidjson/document.h>
#include <Tempest/Vec>

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "game/constants.h"
#include "game/gamescript.h"

class Npc;
class World;

// NPCs that follow their routine, as seen by the AI server's planner (protocol v5):
// - npc_roster: npc's around the player, twice a second;
// - npc_event:  what they notice about the player, and threats from others: monsters near or attacking,
//               armed npc's (from the engine's own perceptions, which still run); a monster killed
//               (`monster_killed`: the danger is over);
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
    // threats to free npc's (monsters near or attacking, attacks by npc's) are held back from the scripts
    // for the server's behavioral model; it answers with a verdict, or the scripts get them after HoldMs
    bool intercept(World& world, Npc& self, Npc& other, Npc* victim, PercType perc);
    void verdict(World& world, const rapidjson::Value& msg);
    void setHoldThreats(bool on) { holdThreats = on; }
    // the server is gone: every held threat goes to the scripts now
    void releaseAll(World& world);
    // `routine`: an npc's daily routine, its routine variants and current state (v6)
    void routine(World& world, const rapidjson::Value& msg);
    void clear();
    // A running soft act already owns movement/queues; a body lease must not overlap it.
    bool isActing(std::string_view symbol) const;

  private:
    struct Running final {
      std::string id;
      std::string symbol;
      size_t      instance = 0;
      uint64_t    start    = 0;
      ScriptFn    state;            // fight/flee: done when the npc leaves this script state
      bool        entered  = false;
      uint64_t    maxMs    = 0;
      // give (v15): the item changes hands once the walk and the turn are done
      size_t      item     = 0;
      size_t      count    = 0;
      size_t      giveTo   = size_t(-1);  // the receiver's instance
      // start_activity with `arrive` (v15): "arrived" once it does the activity within `radius` of `at`
      bool          arrival = false;
      Tempest::Vec3 at;
      float         radius  = 0;
      };

    void emitRoster(World& world);
    // true when the player just came close: the caller emits player_near after the roster
    bool trackProximity(World& world, Npc& npc, std::string_view symbol, float dist);
    void checkRunning(World& world, uint64_t now);
    void emitEvent(std::string_view npc, std::string_view kind, std::string_view other,
                   double distance, std::string_view detail, std::string_view otherName,
                   std::string_view victim = {});
    bool throttled(std::string_view npc, std::string_view kind, uint64_t now, uint64_t periodMs);
    void reply(std::string_view id, std::string_view npc, std::string_view status, std::string_view detail);

    Emit                                  emit;
    Owner                                 owner;
    uint64_t                              nextRoster = 0;
    std::unordered_map<std::string,bool>  close;      // npc -> the player is within reach
    std::unordered_map<std::string,uint64_t> lastEvent;  // "npc/kind" -> time
    std::vector<Running>                  running;

    struct Held final {
      std::string id;
      std::string key;     // "npc/other": a verdict covers that pair for a while
      const Npc*  self   = nullptr;
      const Npc*  other  = nullptr;
      const Npc*  victim = nullptr;
      PercType    perc   = PERC_None;
      uint64_t    at     = 0;
      };
    struct Verdict final {
      bool     server = false;  // the server acts on it (swallow repeats); false: the scripts get them
      uint64_t until  = 0;
      };
    void releaseHeld(World& world, Held& h);

    bool                                     holdThreats = false;
    uint64_t                                 nextHold    = 1;
    std::vector<Held>                        held;
    std::unordered_map<std::string,Verdict>  verdicts;
  };
