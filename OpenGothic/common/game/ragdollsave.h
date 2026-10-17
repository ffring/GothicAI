#pragma once

#include "physics/ragdoll.h"

#include <optional>

class Serialize;

// A separate optional NPC archive entry. Missing entries from older saves are
// handled by Npc; this codec never changes the existing NPC data/visual layout.
// State v1 stores uniformly scaled visual frames. State v2 stores unit native
// frames plus constant initial affine bone frames, preserving anisotropic rigs.
// State v3 additionally preserves the native COM metres, quaternion and velocity
// float bits; public cm frames remain independently checked by the backend.
// Its ancestor records keep unphysical root/ancestor bones attached to physical
// parts without introducing extra bodies; their topology is checked on restore.
namespace RagdollSave {
  struct QuestReturn final {
    static constexpr float maxFall = 1000.f; // centimetres below the last dry support.
    static constexpr uint8_t fell = 1, water = 2;
    Tempest::Vec3 anchor = {};
    float angle = 0;
    bool hasAnchor = false;
    bool armed = false;
    uint8_t hazards = 0;
    };

  void saveQuestReturn(Serialize& fout, const QuestReturn& state);
  bool loadQuestReturn(Serialize& fin, QuestReturn& state);

  struct Snapshot final {
    bool                          legacyDown = false;
    std::optional<Ragdoll::State> body;
    };

  bool valid(const Snapshot& snapshot);
  // Invalid input is rejected before any bytes are written.
  void save(Serialize& fout, const Snapshot& snapshot);
  // Reads into a temporary snapshot; malformed input leaves the caller unchanged.
  bool load(Serialize& fin, Snapshot& snapshot);
  }
