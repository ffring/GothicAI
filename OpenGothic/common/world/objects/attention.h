#pragma once

#include <Tempest/Vec>

#include <cstdint>

class Npc;
class World;

// Attention: whom an npc looks at when it talks to nobody and no script tells it where to look.
// The hero coming close, someone with a weapon drawn, a fight nearby catch its eye; it looks a few
// seconds, loses interest, glances again later. The gaze (graphics/mesh/gaze) turns the head and the
// body; this only picks the one to look at.
class Attention final {
  public:
    void tick(Npc& self, World& world);
    void clear();
    Npc* target() const { return current; }

  private:
    bool     canLook(const Npc& self) const;
    float    interest(const Npc& self, const Npc& other, uint64_t now) const;
    uint64_t random(const Npc& self, uint64_t from, uint64_t to);

    Npc*     current   = nullptr;
    uint64_t lookUntil = 0;       // then it loses interest...
    Npc*     tired     = nullptr; // ...in this one, standing there
    Tempest::Vec3 tiredAt = {};
    uint64_t restUntil = 0;       // and does not look at it again until
    uint64_t nextCheck = 0;
    uint32_t counter   = 0;
  };
