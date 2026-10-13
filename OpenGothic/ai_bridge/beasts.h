#pragma once

#include <rapidjson/document.h>

#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <unordered_map>

#include "game/constants.h"

class Npc;
class World;

// Wolves hear the hero's voice (docs/beasts.md, protocol v19). A wolf understands no words, only how he
// says them; the server judges that, the bridge carries the wolf's answer out with the game's own means.
// - beasts:     the wolves near the hero and what they do about him (they threaten him, attack him,
//               listen, let him pass...), twice a second while there are any;
// - beast:      the server's word on a wolf:
//               listen  it waits for his words (the talk key is held): perks up, does not attack unless
//                       he comes close;
//               calm    it lets him pass: stands (or sits) watching him, growls if he comes near, bites if
//                       he comes closer still or hits it or a wolf near it; once he is gone it goes back to
//                       its routine and leaves him be a while;
//               flee    it runs from him (ZS_MM_FLEE), and leaves him be a while;
//               attack  it attacks him now (ZS_MM_ATTACK);
//               resume  back to its scripts;
// - beast_event: what the bridge ended itself (calm_over, calm_broken, listen_over).
// Ids are the wolf's symbol and a number of this session ("WOLF#3"): wolves share their symbols.
class BeastWatch final {
  public:
    using Emit = std::function<void(std::string&&)>;
    explicit BeastWatch(Emit emit);

    void tick(World& world);
    bool command(World& world, const rapidjson::Value& msg, std::string& err);
    // the console (`ai beast <do> [ms]`, the test bench): the word on the wolf nearest the hero, no server
    std::string console(World& world, std::string_view what, int64_t ms, bool sit);
    // perceptions that would set a free wolf on the hero while it leaves him be: true swallows them
    bool intercept(World& world, Npc& self, Npc& other, Npc* victim, PercType perc);
    // the server is gone: every wolf back to its scripts
    void releaseAll(World& world);
    // the world is gone already: the pointers dangle
    void clear();
    std::string status() const;

  private:
    enum class Mode : uint8_t { Free, Listen, Calm };
    struct Wolf final {
      uint32_t    serial        = 0;
      uint32_t    instance      = 0;  // a pointer reused by another npc is not taken for this wolf
      Mode        mode          = Mode::Free;
      uint64_t    since         = 0;  // listen, calm
      uint64_t    until         = 0;
      uint64_t    nextAni       = 0;  // its next growl or look
      uint64_t    busyUntil     = 0;  // an animation of ours plays till then
      uint64_t    loggedAt      = 0;
      uint64_t    tolerateUntil = 0;  // it leaves the hero be till then
      uint64_t    heroHitAt     = 0;  // the hero hit it last
      uint64_t    seenAt        = 0;  // found near the hero last
      int32_t     hp            = -1;
      bool        sit           = false;  // calm: it sits down while he keeps his distance
      bool        sitting       = false;
      bool        warned        = false;  // calm: it growled at him for coming near
      bool        backing       = false;  // calm: it steps back from him first (it was at his throat)
      };

    Wolf*       find(std::string_view id, Npc*& npc);
    Npc*        locate(World& world, Npc* ptr, const Wolf& w);  // the wolf's npc if still in the world
    std::string idOf(World& world, const Npc& npc, const Wolf& w) const;
    std::string stateOf(World& world, Npc& npc, const Wolf& w, Npc& hero) const;
    bool        apply(World& world, Npc& npc, Wolf& w, std::string_view what, int64_t ms, bool sit, std::string& err);
    void        take(World& world, Npc& npc, Wolf& w, Mode mode, uint64_t now, uint64_t ms);
    bool        play(World& world, Npc& npc, Wolf& w, std::string_view ani, BodyState bs, uint64_t now);
    void        release(World& world, Npc& npc, Wolf& w, std::string_view to, uint64_t now);
    void        drive(World& world, Npc& npc, Wolf& w, Npc& hero, uint64_t now);
    void        event(World& world, const Npc& npc, const Wolf& w, std::string_view kind, std::string_view detail);
    void        report(World& world, Npc& hero, uint64_t now);

    Emit                          emit;
    std::unordered_map<Npc*,Wolf> wolves;  // pointers are compared, followed only once found in the world
    uint32_t                      serials    = 0;
    uint64_t                      nextReport = 0;
    bool                          listed     = false;  // the last report was not empty
  };
