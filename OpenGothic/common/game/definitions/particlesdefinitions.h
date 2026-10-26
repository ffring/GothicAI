#pragma once

#include <zenkit/DaedalusVm.hh>
#include <zenkit/addon/daedalus.hh>

#include <unordered_map>
#include <memory>
#include <mutex>

#include "graphics/visualfx.h"

class ParticleFx;

// The look of the stream (judged on bench stills, scripts/bench/humiliate-look.bench): velocity in cm/ms,
// gravity in cm/ms^2, angles in degrees in the pelvis bone's frame
namespace PeeStream {
  inline constexpr float pps        = 120;
  inline constexpr float head       = 0;
  inline constexpr float elev       = 35;
  inline constexpr float spread     = 2;
  inline constexpr float vel        = 0.5f;
  inline constexpr float lifeMs     = 1500;
  inline constexpr char  offset[]     = "0 0 0";
  inline constexpr char  gravity[]    = "0 -0.0009 0";
  inline constexpr char  colorStart[] = "255 215 50";
  inline constexpr char  colorEnd[]   = "240 195 40";
  inline constexpr char  size[]       = "8 8";
  }

class ParticlesDefinitions final {
  public:
    ParticlesDefinitions();
    ~ParticlesDefinitions();

    const ParticleFx* get(std::string_view name, bool relaxed);
    const ParticleFx* get(const ParticleFx* base, const VisualFx::Key* key);

  private:
    std::recursive_mutex                sync;
    std::unique_ptr<zenkit::DaedalusVm> vm;

    std::unordered_map<std::string,          std::unique_ptr<ParticleFx>> pfx;
    std::unordered_map<const VisualFx::Key*, std::unique_ptr<ParticleFx>> pfxKey;

    const ParticleFx* implGet(std::string_view name, bool relaxed);
    const ParticleFx* implGet(const ParticleFx& base, const VisualFx::Key& key);

    std::shared_ptr<zenkit::IParticleEffect> implGetDirect(std::string_view name, bool relaxed);
    std::shared_ptr<zenkit::IParticleEffect> peeStream();
  };
