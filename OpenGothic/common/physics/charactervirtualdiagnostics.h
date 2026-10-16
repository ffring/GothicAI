#pragma once

#include <Tempest/Vec>
#include <cstdint>
#include <string>
#include <vector>

// Opt-in, transient bench data. Positions/distances use cm, linear velocities
// cm/s, angular velocities rad/s. Resolve payload pointers when taking a sample;
// copied diagnostics do not own the game objects to which those pointers refer.
struct CharacterVirtualDiagnostics {
  struct Body {
    uint32_t id = uint32_t(-1);
    int layer = 0, pointerType = 0, motionType = 0;
    uintptr_t nativeUserData = 0;
    void* payload = nullptr;
    std::string label;
    float mass = 0.f;
    Tempest::Vec3 position = {}, linearVelocity = {}, angularVelocity = {}, pointVelocity = {};
    bool present = false;
    };

  struct Contact {
    Body body;
    uint32_t subShape = uint32_t(-1);
    uintptr_t contactUserData = 0;
    Tempest::Vec3 position = {}, contactNormal = {}, surfaceNormal = {}, pointVelocity = {};
    float distance = 0.f, supportingVolumeDistance = 0.f;
    bool hadCollision = false, discarded = false, sensor = false, canPushCharacter = false;
    bool withinSupportingVolume = false, walkableSlope = false, supportAllowedByPolicy = false;
    // Individual walkable support. Jolt can also support a character through a
    // combination of steep contacts, which is described by pose.groundState.
    bool supportEligible = false;
    };

  struct Pose {
    Tempest::Vec3 position = {}, velocity = {}, groundPosition = {}, groundNormal = {}, groundVelocity = {};
    Body ground;
    uint32_t groundSubShape = uint32_t(-1);
    uintptr_t groundUserData = 0;
    uint8_t groundState = 0; // Jolt: 0 OnGround, 1 OnSteepGround, 2 NotSupported, 3 InAir.
    std::vector<Contact> contacts;
    };

  uint64_t dt = 0;
  Tempest::Vec3 requested = {};
  Pose before, afterExtendedUpdate, afterStairs;
  // ExtendedUpdate includes native floor sticking; automatic native stairs are
  // disabled there. afterStairs is the separate world-stair recipe's result.
  bool stairAttempted = false, stairSucceeded = false, valid = false;
  };
