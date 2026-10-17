#pragma once

#include <memory>
#include <string_view>

class World;

// Optional, local-only navigation telemetry. No server connection or bridge protocol is involved.
class StuckLogger final {
  public:
    static constexpr float NativeAiRadiusCm = 3000.f;
    static std::unique_ptr<StuckLogger> fromEnvironment();
    ~StuckLogger();

    static bool beginObservation(World& world, std::string_view label, float radius=NativeAiRadiusCm);
    static bool endObservation(World& world);

    StuckLogger(const StuckLogger&) = delete;
    StuckLogger& operator=(const StuckLogger&) = delete;

  private:
    explicit StuckLogger(const char* path);
    struct Impl;
    std::unique_ptr<Impl> impl;
  };
