#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

class Npc;

// Simulation-thread telemetry only. A bounded in-memory queue separates the native executor
// from the Linux recorder's durable writer; no files, locks, waits or game mutations live here.
class BodyTrajectory final {
  public:
    static void configure(bool enabled, size_t capacity = 4096);
    static void reset(std::string_view reason = "reset");
    static bool enabled();
    static void observe(std::string_view observation) noexcept;
    static void noteControl(std::string_view controlEvent) noexcept;
    static void beginNative(Npc& actor, int intent, int effective, uint64_t parentChoice,
                            int parentInstruction, uint64_t entrySerial) noexcept;
    static void nativeAfterAttempt(Npc& actor) noexcept;
    static void nativeConsumed(Npc& actor) noexcept;
    static void postStep() noexcept;
    static void forget(const Npc* actor) noexcept;
    static std::string drainJson(size_t max = 256);
    static std::string statsJson();

    class Attempt final {
      public:
        Attempt(Npc& actor, bool native, int intent, int effective, uint64_t parentChoice,
                int parentInstruction, uint64_t entrySerial) noexcept;
        ~Attempt() noexcept;
        Attempt(const Attempt&) = delete;
        Attempt& operator=(const Attempt&) = delete;
      private:
        Npc* actor = nullptr;
      };
  private:
    static void beginNativeImpl(Npc& actor, int intent, int effective, uint64_t parentChoice,
                                int parentInstruction, uint64_t entrySerial);
    static void nativeAfterAttemptImpl(Npc& actor);
    static void nativeConsumedImpl(Npc& actor);
    static void postStepImpl();
    static void forgetImpl(const Npc* actor);
  };
