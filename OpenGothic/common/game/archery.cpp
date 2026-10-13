#include "archery.h"

#include <Tempest/Log>

#include <algorithm>
#include <cmath>
#include <cstdlib>

#include "world/objects/npc.h"
#include "world/objects/item.h"
#include "physics/dynamicworld.h"
#include "gothic.h"

using namespace Tempest;

namespace {

constexpr float Deg = float(M_PI)/180.f;

// the table to balance: a novice (skill 0) and a master (skill 1)
constexpr float SwayNovice     = 0.9f;  // degrees, RMS at full draw (each way)
constexpr float SwayMaster     = 0.25f;
constexpr float SettleNovice   = 0.6f;  // seconds
constexpr float SettleMaster   = 0.2f;
constexpr float BreathNovice   = 2.f;   // seconds
constexpr float BreathMaster   = 5.f;
constexpr float DrawSlow       = 1.2f;  // seconds: dexterity 0..DexFull
constexpr float DrawFast       = 0.5f;
constexpr float DexFull        = 150.f;
constexpr float ReloadSlow     = 0.85f; // the reload's tempo: dexterity 0..DexFull
constexpr float ReloadFast     = 1.6f;
constexpr float HoldWeak       = 1.5f;  // seconds: strength just half of what the bow needs...
constexpr float HoldStrong     = 6.f;   // ...and twice
constexpr float SpeedLight     = 5.0f;  // cm/ms: a bow of DamageLight...
constexpr float SpeedHeavy     = 7.5f;  // ...and of DamageHeavy
constexpr float DamageLight    = 30.f;
constexpr float DamageHeavy    = 150.f;
constexpr float ConeNovice     = 0.6f;  // degrees
constexpr float ConeMaster     = 0.1f;
constexpr float NeedPerDamage  = 0.6f;  // strength a bow needs per its damage: a bow of 100 wants 60
constexpr float NeedMin        = 10.f;
constexpr float SlowSpeed      = 0.4f;  // of the speed at no draw (MinDraw shoots a bit faster)
constexpr float CbowSteady     = 0.8f;  // a crossbow braced at the shoulder sways less than a drawn bow...
constexpr float CbowLight      = 0.9f;  // ...a light one less, a heavy one more (by the strength it needs,
constexpr float CbowHeavy      = 1.3f;  // NeedLight..NeedHeavy)
constexpr float NeedLight      = 20.f;
constexpr float NeedHeavy      = 100.f;
constexpr float CbowSpeed      = 1.3f;  // a bolt flies faster than an arrow of a bow as heavy
constexpr float CReloadSlow    = 0.75f; // the crossbow's reload (cocking): strength half of what it needs...
constexpr float CReloadFast    = 1.4f;  // ...and twice
constexpr float CantDraw       = 0.6f;  // the bow canted (a quick shot): drawn that much faster...
constexpr float CantSettle     = 0.6f;  // ...settles after a turn that much faster...
constexpr float CantSway       = 1.3f;  // ...but sways more (no anchor, no sight over the arrow)...
constexpr float CantCone       = 0.3f;  // ...and the release throws it off by that many degrees more
constexpr float HeadDamage     = 2.f;
constexpr float LimbDamage     = 0.6f;

int enabled = -1; // -1: as Gothic.ini says

float lerp(float a, float b, float t) {
  return a + (b-a)*std::clamp(t,0.f,1.f);
  }

}

bool Archery::isEnabled() {
  if(enabled<0) {
    // OPENGOTHIC_MANUALAIM=0|1 over Gothic.ini (a before/after short films the auto aim)
    const char* v = std::getenv("OPENGOTHIC_MANUALAIM");
    if(v!=nullptr && v[0]!='\0')
      enabled = v[0]!='0' ? 1 : 0; else
      enabled = Gothic::settingsGetI("GAME","manualAim")!=0 ? 1 : 0;
    }
  return enabled!=0;
  }

void Archery::setEnabled(bool e) {
  enabled = e ? 1 : 0;
  Log::i("manual aim: ", e ? "on" : "off");
  }

Archery::Stats Archery::stats(Npc& npc, const Item* w) {
  Stats s;
  s.crossbow = w!=nullptr && w->isCrossbow();

  const bool  g2  = Gothic::inst().version().game==2;
  const float dex = float(npc.attribute(ATR_DEXTERITY));
  const float str = float(npc.attribute(ATR_STRENGTH));
  if(g2)
    s.skill = float(npc.hitChance(s.crossbow ? TALENT_CROSSBOW : TALENT_BOW))/100.f; else
    s.skill = dex/100.f;
  s.skill = std::clamp(s.skill,0.f,1.f);

  // how heavy the weapon is, in strength: from its damage, or what it asks for
  const float dmg  = w!=nullptr ? float(w->handle().damage_total) : DamageLight;
  float       need = dmg*NeedPerDamage;
  if(w!=nullptr) {
    auto& h = w->handle();
    for(size_t i=0; i<std::size(h.cond_atr); ++i)
      if(h.cond_atr[i]==ATR_STRENGTH)
        need = std::max(need,float(h.cond_value[i]));
    }
  need       = std::max(need,NeedMin);
  s.strength = str/need;

  const float weak = std::max(0.f,1.f-s.strength);
  const float dexK = dex/DexFull;
  s.sway      = lerp(SwayNovice,SwayMaster,s.skill)*(1.f+weak)*Deg;
  if(s.crossbow)
    s.sway *= CbowSteady*lerp(CbowLight,CbowHeavy,(need-NeedLight)/(NeedHeavy-NeedLight));
  s.settle    = lerp(SettleNovice,SettleMaster,s.skill)*lerp(1.2f,0.8f,dexK);
  s.breath    = lerp(BreathNovice,BreathMaster,s.skill);
  s.drawTime  = s.crossbow ? 0.f : lerp(DrawSlow,DrawFast,dexK)*std::clamp(1.f/std::sqrt(std::max(s.strength,0.01f)),0.8f,2.f);
  s.holdLimit = s.crossbow ? 1e6f : lerp(HoldWeak,HoldStrong,(s.strength-0.5f)/1.5f);
  s.speed     = lerp(SpeedLight,SpeedHeavy,(dmg-DamageLight)/(DamageHeavy-DamageLight))*(s.crossbow ? CbowSpeed : 1.f);
  s.cone      = lerp(ConeNovice,ConeMaster,s.skill)*Deg;
  s.reload    = s.crossbow ? lerp(CReloadSlow,CReloadFast,(s.strength-0.5f)/1.5f) : lerp(ReloadSlow,ReloadFast,dexK);
  return s;
  }

Archery::Stats Archery::canted(const Stats& s, float k) {
  Stats c = s;
  k = std::clamp(k,0.f,1.f);
  if(s.crossbow || k<=0.f)
    return c;
  c.drawTime *= lerp(1.f,CantDraw,k);
  c.settle   *= lerp(1.f,CantSettle,k);
  c.sway     *= lerp(1.f,CantSway,k);
  c.cone     += CantCone*Deg*k;
  return c;
  }

Tempest::Vec3 Archery::launch(const Vec3& from, const Vec3& to, float speed) {
  // the low one of the two arcs: tan = (v^2 - sqrt(v^4 - g(g x^2 + 2 y v^2)))/(g x)
  const Vec3  d   = to-from;
  const float x   = std::sqrt(d.x*d.x + d.z*d.z);
  const float len = d.length();
  if(x<1.f || speed<=0.f)
    return to;
  const float g    = DynamicWorld::gravity;
  const float v2   = speed*speed;
  const float disc = v2*v2 - g*(g*x*x + 2.f*d.y*v2);
  const float tan  = disc>=0.f ? (v2-std::sqrt(disc))/(g*x) : 1.f;
  const float c    = 1.f/std::sqrt(1.f+tan*tan);
  return from + Vec3(d.x/x*c, tan*c, d.z/x*c)*len;
  }

float Archery::speedAt(const Stats& s, float draw) {
  return s.speed*lerp(SlowSpeed,1.f,draw);
  }

float Archery::damageAt(float draw) {
  return std::clamp(draw,0.f,1.f);
  }

float Archery::zoneDamage(HitZones::Zone z) {
  switch(z) {
    case HitZones::Head: return HeadDamage;
    case HitZones::Limb: return LimbDamage;
    case HitZones::Body:
    case HitZones::None: return 1.f;
    }
  return 1.f;
  }
