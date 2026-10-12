#include "aim.h"

#include <Tempest/Log>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string_view>

#include "ikmath.h"
#include "skeleton.h"

using namespace Tempest;
using namespace IkMath;

namespace {

constexpr float Deg         = float(M_PI)/180.f;
constexpr float MaxTurn     = 60.f*Deg;  // the upper body turns at most that far to the target
constexpr float WaistShare  = 0.45f;     // part of the turn the waist does, the chest the rest
constexpr float TrackRate   = 16.f;      // 1/s, how fast the aim follows the target...
constexpr float MaxTrack    = 240.f*Deg; // ...but not faster than this, per second
constexpr float BodyRate    = 16.f;      // 1/s, how fast the upper body takes a new turn
constexpr float MaxBody     = 180.f*Deg; // per second
constexpr float SteadyAngle = 1.5f*Deg;  // the arrow moved less than this in a frame...
constexpr int   SteadyFrames= 4;         // ...that many frames: the bow holds still
constexpr float LeanRate    = 6.f;       // 1/s, leaning over an edge...
constexpr float MaxLeanRate = 90.f*Deg;  // ...not faster than this, per second
constexpr float BlendIn     = 0.2f;      // seconds: the upper body turns to the target...
constexpr float BlendOut    = 0.25f;     // ...and back when the bow goes down
constexpr float Snap        = 0.0005f;

bool enabled = []() {
  const char* v = std::getenv("OPENGOTHIC_AIM");
  return v==nullptr || v[0]!='0';
  }();

// angle around Up that turns horizontal unit vector a toward b
float yawBetween(const Vec3& a, const Vec3& b) {
  return std::atan2(dot(cross(Up,a),b), dot(a,b));
  }

float wrap(float a) {
  return std::remainder(a, 2.f*float(M_PI));
  }

// the body's frame: forward, up, side
Vec3 toBody(const Vec3& v, const Vec3& f) {
  return Vec3(dot(v,f), v.y, dot(v,cross(Up,f)));
  }

Vec3 fromBody(const Vec3& v, const Vec3& f) {
  return f*v.x + Up*v.y + cross(Up,f)*v.z;
  }

// bending forward toward where the shot goes (horizontally; straight down: where the body faces)
Vec3 leanDir(const Vec3& want, const Vec3& forward) {
  return normalize(Vec3(want.x,0,want.z) + forward*0.25f);
  }

Mat3 leanRotation(const Vec3& want, const Vec3& forward, float angle) {
  if(angle==0.f)
    return axisAngle(Up,0.f);
  return axisAngle(normalize(cross(Up,leanDir(want,forward))),angle);
  }

void approach(Vec3& v, const Vec3& target, float k, float maxStep, bool& changed) {
  Vec3 d = (target-v)*k;
  if(length(d)>maxStep)
    d = d*(maxStep/length(d));
  if(length(target-v-d)<Snap)
    d = target-v;
  if(length(d)>0.f)
    changed = true;
  v += d;
  }

void approach(float& v, float target, float k, float maxStep, bool& changed) {
  float nv = v + std::clamp(wrap(target-v)*k, -maxStep, maxStep);
  if(std::abs(wrap(target-nv))<Snap)
    nv = target;
  if(nv!=v)
    changed = true;
  v = nv;
  }

}

Aim::Probe Aim::playerProbe;
Aim::Probe Aim::subjectProbe;
const void* Aim::probeNpc = nullptr;

float Aim::leanFor(float down) {
  const float k = std::clamp((down/Deg - LeanFrom)/(LeanFull - LeanFrom), 0.f, 1.f);
  return k*LeanMax*Deg;
  }

bool Aim::isEnabled() {
  return enabled;
  }

void Aim::setEnabled(bool e) {
  enabled = e;
  Log::i("aim: ", e ? "on" : "off");
  }

void Aim::setSkeleton(const Skeleton* sk) {
  *this = Aim();
  if(sk==nullptr)
    return;

  auto& nodes = sk->nodes;
  auto  find  = [&](std::string_view name) {
    for(size_t i=0; i<nodes.size(); ++i)
      if(nodes[i].name==name)
        return i;
    return size_t(-1);
    };
  auto  isUnder = [&](size_t id, size_t root) {
    for(size_t i=id; i<nodes.size(); i=nodes[i].parent)
      if(i==root)
        return true;
    return false;
    };
  auto  tree = [&](size_t root, std::vector<size_t>& out) {
    for(size_t i=0; root!=size_t(-1) && i<nodes.size(); ++i)
      if(isUnder(i,root))
        out.push_back(i);
    };

  spine = find("BIP01 SPINE");
  waist = find("BIP01 SPINE1");
  chest = find("BIP01 SPINE2");
  slot  = find("ZS_RIGHTHAND"); // the arrow, the bolt, the spell sit there
  if(waist==size_t(-1) || chest==size_t(-1) || slot==size_t(-1) || !isUnder(chest,waist) || !isUnder(slot,chest))
    return;
  if(spine==size_t(-1) || !isUnder(waist,spine))
    spine = waist;
  tree(spine,spineTree);
  tree(waist,waistTree);
  tree(chest,chestTree);
  valid = true;
  }

bool Aim::update(const Input& in) {
  if(!valid)
    return false;

  const float dt      = float(in.dt)/1000.f;
  bool        changed = false;
  const Vec3  f       = normalize(Vec3(in.forward.x,0,in.forward.z));
  const Vec3  d       = normalize(in.dir);
  const bool  on      = in.active && enabled && length(f)>0.5f && length(d)>0.5f;

  if(on) {
    forward = f;
    arrow   = in.arrow;
    // a direction, not yaw and pitch: straight down the yaw means nothing
    const Vec3 db = toBody(d,forward);
    if(!ready) {
      wantB = db;
      } else {
      approach(wantB,db,1.f - std::exp(-TrackRate*dt),MaxTrack*dt,changed);
      wantB = normalize(wantB);
      }

    // where the animation aims, measured while the bow holds still (raising it, reloading swing the
    // arrow around), kept in the body's frame; then the turn the upper body needs: the shortest one from
    // there to where the shot has to go, every frame (the target may change while reloading)
    const Vec3 a = arrow ? animArrow : forward;
    const bool still = !arrow || (measured && std::acos(std::clamp(dot(a,prevArrow),-1.f,1.f))<SteadyAngle);
    steadyFrames = still ? uint8_t(std::min(steadyFrames+1,255)) : 0;
    steady       = steadyFrames>=SteadyFrames || !arrow;
    if(steady)
      rest = toBody(a,forward);
    approach(leanAmt,in.lean,1.f - std::exp(-LeanRate*dt),MaxLeanRate*dt,changed);
    if(steady || ready) {
      // where it aims once leaned over the edge
      const Vec3  want = fromBody(wantB,forward);
      const Vec3  ar   = mul(leanRotation(want,forward,leanAmt),fromBody(rest,forward));
      const Vec3  axis = cross(ar,want);
      const float s    = length(axis);
      const float an   = std::min(std::atan2(s,dot(ar,want)), MaxTurn);
      goal = s>1e-4f ? toBody(axis*(an/s),forward) : Vec3();
      if(!ready)
        rot = goal;
      ready = true;
      }
    } else {
    steady       = false;
    steadyFrames = 0;
    }
  prevArrow = animArrow;

  if(!on)
    approach(leanAmt,0.f,1.f - std::exp(-LeanRate*dt),MaxLeanRate*dt,changed);
  if(ready)
    approach(rot,goal,1.f - std::exp(-BodyRate*dt),MaxBody*dt,changed);

  // the weight fades in once the turn is known, out when the bow goes down
  const float prevW = weight;
  if(on && ready)
    weight = std::min(1.f, weight + dt/BlendIn);
  else if(!on)
    weight = std::max(0.f, weight - dt/BlendOut);
  if(weight!=prevW)
    changed = true;
  if(weight<=0.f)
    ready = false; // the bow is down: measure anew next time
  return changed;
  }

Vec3 Aim::arrowOf(const Matrix4x4* tr) const {
  // the arrow (the bolt) lies along -X of the right hand slot, the nock at the string
  return normalize(Vec3(-tr[slot].at(0,0),-tr[slot].at(0,1),-tr[slot].at(0,2)));
  }

void Aim::apply(Matrix4x4* tr, size_t numBones) {
  error = -1;
  if(!valid || spineTree.back()>=numBones || waistTree.back()>=numBones || chestTree.back()>=numBones || slot>=numBones)
    return;
  animArrow = arrowOf(tr);
  measured  = true;
  if(weight<=0.f)
    return;

  // over an edge the trunk bends forward from the hips toward the target first
  const Vec3  want = fromBody(wantB,forward);
  const float l    = leanAmt*weight;
  if(l>0.f)
    rotateAbout(tr,spineTree,leanRotation(want,forward,l),position(tr[spine]));

  // the upper body turns from where it aims to the target: the waist a part, the chest the rest
  const Vec3  r  = fromBody(rot,forward);
  const float an = length(r)*weight;
  if(an>0.f) {
    const Vec3 axis = normalize(r);
    rotateAbout(tr,waistTree,axisAngle(axis,an*WaistShare),       position(tr[waist]));
    rotateAbout(tr,chestTree,axisAngle(axis,an*(1.f-WaistShare)), position(tr[chest]));
    }

  const Vec3 now  = arrow ? arrowOf(tr) : (an>0.f ? mul(axisAngle(normalize(r),an),forward) : forward);
  error = std::acos(std::clamp(dot(now,want),-1.f,1.f))/Deg;
  }

void Aim::probe(Probe& p) const {
  const Vec3 a  = arrow ? animArrow : forward;
  const Vec3 ah = Vec3(a.x,0,a.z);
  p.valid     = valid;
  p.active    = weight>0.f;
  p.steady    = steady;
  p.weight    = weight;
  const Vec3 w  = fromBody(wantB,forward);
  const Vec3 wh = Vec3(w.x,0,w.z);
  p.yaw       = length(wh)>1e-3f ? yawBetween(forward,normalize(wh))/Deg : 0.f;
  p.pitch     = std::asin(std::clamp(w.y,-1.f,1.f))/Deg;
  p.animYaw   = length(ah)>0.05f ? yawBetween(forward,normalize(ah))/Deg : 0.f;
  p.animPitch = std::asin(std::clamp(a.y,-1.f,1.f))/Deg;
  p.turn      = rot.y*weight/Deg;   // about up, + to the right
  p.bend      = -rot.z*weight/Deg;  // about the side, + up
  p.lean      = leanAmt*weight/Deg;
  p.error     = error;
  }
