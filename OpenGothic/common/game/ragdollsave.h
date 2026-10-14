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
