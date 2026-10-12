#pragma once

#include <rapidjson/document.h>

#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <unordered_set>
#include <vector>

class Npc;
class World;

// The director's hands and eyes in the world (docs/story-graph.md, stage 4): creatures it puts under a
// tag (the wolves of a task), the deaths it learns about with the killer, and effects on an npc (an
// attack, refusing to talk, items). Tags live as long as the world session: a load drops them.
class StoryWorld final {
  public:
    // send: a message to the server; release: hands an npc held by the server back to its scripts
    // (an attack must run in them)
    StoryWorld(std::function<void(std::string&&)> send, std::function<void(World&,Npc&)> release);

    // story_spawn: `count` creatures of `instance`, `distance` cm from an npc (`near`, or the player),
    // under `tag`; returns how many came
    int  spawn(World& world, const rapidjson::Value& msg, std::string& error);
    // story_watch: npcs whose distance to each tag's living creatures is reported (`npcs`, replaces)
    void watch(const rapidjson::Value& msg);
    // story_effect: effects on `npc`, as self, with the hero as other
    bool effects(World& world, const rapidjson::Value& msg, std::string& error);
    // story_nearby: people within `radius` cm of `npc` (alive, not the hero), as JSON
    // [{"npc", "name", "distance", "guild"}]; empty if there is no such npc
    std::string nearby(World& world, const rapidjson::Value& msg);
    // story_state: what the director's rules read (stage 5), as JSON: values of global int `vars`, whether
    // `npcs` live ({"state": "alive", "dead" or "absent", "name"}), which dialogs (`knows`) the hero has
    // heard, what script `symbols` are ("npc", "item", "info", "var", "const", "func", "other"; missing:
    // absent), and whether a dialog or a cutscene runs now
    std::string state(World& world, const rapidjson::Value& msg);
    // deaths of people and of tagged creatures (npc_died) and, once a second, the tags (story_world)
    void tick(World& world);
    // a load or a new world: what was tracked belongs to the old one
    void clear();

  private:
    void report(World& world);

    std::function<void(std::string&&)>     send;
    std::function<void(World&,Npc&)>       release;
    std::map<std::string,std::vector<Npc*>> tags;
    std::vector<std::string>               watchers;  // npc symbols
    std::unordered_set<const Npc*>         dead;      // deaths already known
    bool                                   primed = false;  // the dead of a loaded world are not news
    uint64_t                               nextScan = 0, nextReport = 0;
  };
