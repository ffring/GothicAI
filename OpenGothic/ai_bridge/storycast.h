#pragma once

#include <rapidjson/document.h>

#include <string>

class World;

// The director's own characters in the world (docs/director.md, stage 2, layer 2). story_npc describes one:
// the script's npc it is made like, its name, guild, look, things and day, and where it comes. The game makes
// it once: an npc of ours (OwnSymbols, a symbol of its own) put at its point, then named, dressed, given its
// things and its day. Asked again (the server sends its branch's characters every session), an npc the world
// has is left as it is: the save remembers what became of it.
class StoryCast final {
  public:
    // story_npc: JSON {"npc", "state": "made" | "there" | "dead" | "absent", "index": its symbol}; empty and
    // `error` if it can't be made (nothing is made then, unless the template's code gives it no body)
    static std::string npc(World& world, const rapidjson::Value& msg, std::string& error);
  };
