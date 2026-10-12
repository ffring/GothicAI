#pragma once

#include <rapidjson/document.h>

#include <cstdint>
#include <functional>
#include <string>

class Npc;
class World;

// What an npc perceives right now, for the AI server's two models (protocol v11; v12: the game time of a
// view, the nearest waypoint of whom it sees):
// - `npc_view{req, npcs}`: one view per npc, as the result's value;
// - `npc_views{t, views}`: once a second, the views of the npc's nearest to the player.
// A view: where the npc is (indoors, whose room), what it does and holds; whom it sees and what they look
// like and do; the player, seen or not (it hears him). Sight is the engine's own, as the scripts have it
// (Npc::canSeeNpc: the head's field of view and a ray), within the npc's senses range.
class NpcView final {
  public:
    using Emit = std::function<void(std::string&&)>;

    explicit NpcView(Emit emit);

    void tick(World& world);
    // a JSON array of the views of the npc's named in `npcs` (script symbols); unknown ones are left out
    std::string views(World& world, const rapidjson::Value& npcs);

  private:
    Emit     emit;
    uint64_t next = 0;
  };

// whether `self` sees `other` now: the engine's own sight, never further than npc_view looks
bool npcSees(const Npc& self, const Npc& other);
