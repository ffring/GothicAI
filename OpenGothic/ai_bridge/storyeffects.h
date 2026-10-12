#pragma once

#include <rapidjson/document.h>

#include <cstdint>
#include <string>
#include <string_view>

class Npc;

namespace zenkit {
  class DaedalusVm;
  }

// What the director makes happen, through the scripts' own functions (docs/story-graph.md, stages 3–4):
// in a dialog of its own, or as the consequence of a lie found out.
struct StoryEffect final {
  std::string kind;     // give, xp, set, journal, end, attack, refuse_talk
  std::string item;     // give: item instance
  std::string var;      // set: global int variable
  std::string topic;    // journal: topic, game codepage
  std::string text;     // journal: entry, game codepage
  std::string status;   // journal: LOG_RUNNING (default), LOG_SUCCESS, LOG_FAILED, LOG_OBSOLETE
  std::string reason;   // attack: AR_* constant (default AR_NONE: until the hero is down, not to death)
  int32_t     count  = 1;  // give: count; xp: amount; set: value; refuse_talk: seconds
  bool        toHero = true;  // give: the npc gives to the hero, else the hero to the npc

  // `{"do": "give", "item": ..., ...}`; an error if it is malformed or, with a VM, names something
  // the scripts don't have
  static std::string read(const rapidjson::Value& v, zenkit::DaedalusVm* vm, StoryEffect& e);
  // with the npc as self and the hero as other; failures are logged with `who`
  void run(zenkit::DaedalusVm& vm, Npc& npc, Npc& player, std::string_view who) const;
  };
