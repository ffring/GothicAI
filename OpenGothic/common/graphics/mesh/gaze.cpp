#include "gaze.h"

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

constexpr float Deg          = float(M_PI)/180.f;
constexpr float MaxHeadYaw   = 65.f*Deg;  // the head alone
constexpr float MaxChestYaw  = 25.f*Deg;  // the chest joins for larger turns...
constexpr float MaxWaistYaw  = 20.f*Deg;  // ...and the waist
constexpr float HeadShare    = 0.7f;      // of a turn the head takes this part, the rest the body
constexpr float GiveUp       = 150.f*Deg; // the partner further behind the back: stop looking
constexpr float MaxPitchUp   = 30.f*Deg;
constexpr float MaxPitchDown = 35.f*Deg;
constexpr float ChestPitch   = 0.25f;     // part of looking up or down the chest does
constexpr float TurnRate     = 6.f;       // 1/s, how fast the turn follows the partner
constexpr float BlendTime    = 0.4f;      // seconds, turning to the partner and back...
constexpr float MaxTurnSpeed = 180.f*Deg; // ...but not faster than this: a large turn takes longer
constexpr float Snap         = 0.002f;

bool enabled = []() {
  const char* v = std::getenv("OPENGOTHIC_GAZE");
  return v==nullptr || v[0]!='0';
  }();

void approach(float& v, float target, float k, bool& changed) {
  float nv = v + (target-v)*k;
  if(std::abs(target-nv)<Snap)
    nv = target;
  if(nv!=v)
    changed = true;
  v = nv;
  }

Vec3 axis(const Matrix4x4& m, int k) {
  return normalize(Vec3(m.at(k,0), m.at(k,1), m.at(k,2)));
  }

// angle around Up that turns horizontal unit vector a toward b
float yawBetween(const Vec3& a, const Vec3& b) {
  return std::atan2(dot(cross(Up,a),b), dot(a,b));
  }

}

Gaze::Probe Gaze::playerProbe;
Gaze::Probe Gaze::subjectProbe;
const void* Gaze::probeNpc = nullptr;

bool Gaze::isEnabled() {
  return enabled;
  }

void Gaze::setEnabled(bool e) {
  enabled = e;
  Log::i("gaze: ", e ? "on" : "off");
  }

void Gaze::setSkeleton(const Skeleton* sk) {
  *this = Gaze();
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

  head  = find("BIP01 HEAD");
  chest = find("BIP01 SPINE2");
  waist = find("BIP01 SPINE1");
  if(head==size_t(-1))
    return;
  if(chest!=size_t(-1) && !isUnder(head,chest))
    chest = size_t(-1);
  if(waist!=size_t(-1) && (chest==size_t(-1) || !isUnder(chest,waist)))
    waist = size_t(-1);
  tree(head, headTree);
  tree(chest,chestTree);
  tree(waist,waistTree);
  valid = true;
  }

bool Gaze::update(const Input& in) {
  if(!valid)
    return false;

  const float dt      = float(in.dt)/1000.f;
  bool        changed = false;
  bool        on      = in.active && enabled;
  headOnly = in.headOnly;

  if(on) {
    forward = normalize(Vec3(in.forward.x,0,in.forward.z));
    target  = in.target;
    const Vec3 d  = target - headPos;
    const Vec3 dh = Vec3(d.x,0,d.z);
    const float y = length(dh)>1.f ? yawBetween(forward,normalize(dh)) : 0.f;
    if(length(dh)<=1.f || length(forward)<0.5f || std::abs(y)>GiveUp) {
      on = false;
      } else {
      const float maxYaw = headOnly ? MaxHeadYaw : MaxHeadYaw+MaxChestYaw+MaxWaistYaw;
      yawGoal   = std::clamp(y, -maxYaw, maxYaw);
      pitchGoal = std::clamp(std::atan2(d.y,length(dh)), -MaxPitchDown, MaxPitchUp);
      }
    }

  const float prevW = weight;
  const float angle = std::max(std::abs(on && prevW<=0.f ? yawGoal : yaw), Snap);
  const float dw    = std::min(dt/BlendTime, MaxTurnSpeed*dt/angle);
  if(on)
    weight = std::min(1.f, weight + dw); else
    weight = std::max(0.f, weight - dw);
  if(weight!=prevW)
    changed = true;
  if(weight<=0.f) {
    yaw   = 0;
    pitch = 0;
    return changed;
    }
  if(prevW<=0.f) {
    // turning to the partner: the weight fades the turn in
    yaw   = yawGoal;
    pitch = pitchGoal;
    return true;
    }
  if(on) {
    const float k = 1.f - std::exp(-TurnRate*dt);
    approach(yaw,  yawGoal,  k,changed);
    approach(pitch,pitchGoal,k,changed);
    }
  return changed;
  }

void Gaze::apply(Matrix4x4* tr, size_t numBones) {
  if(!valid || head>=numBones || (!headTree.empty() && headTree.back()>=numBones) ||
     (!chestTree.empty() && chestTree.back()>=numBones) || (!waistTree.empty() && waistTree.back()>=numBones))
    return;
  headPos = position(tr[head]);

  // the axis of the head bone the face looks along: the one closest to the body facing, as animated
  if(length(forward)>0.5f) {
    float best = 0;
    for(int k=0; k<3; ++k) {
      const float c = dot(axis(tr[head],k),forward);
      if(std::abs(c)>std::abs(best)) {
        best     = c;
        faceAxis = k;
        faceSign = c>0 ? 1.f : -1.f;
        }
      }
    }

  parts[0] = parts[1] = parts[2] = 0;
  error    = -1;
  if(weight>0.f) {
    const float y = yaw*weight;
    const float p = pitch*weight;
    // the head turns first, the chest and the waist take what is left (not with the head only)
    float hy = std::clamp(y*(headOnly || chest==size_t(-1) ? 1.f : HeadShare), -MaxHeadYaw, MaxHeadYaw);
    float cy = 0, wy = 0;
    if(!headOnly && chest!=size_t(-1)) {
      const float rest = y - hy;
      cy = std::clamp(rest*(waist==size_t(-1) ? 1.f : 0.55f), -MaxChestYaw, MaxChestYaw);
      wy = waist==size_t(-1) ? 0.f : std::clamp(rest-cy, -MaxWaistYaw, MaxWaistYaw);
      hy = std::clamp(y-cy-wy, -MaxHeadYaw, MaxHeadYaw);
      }
    if(wy!=0.f)
      rotateAbout(tr,waistTree,axisAngle(Up,wy),position(tr[waist]));
    if(cy!=0.f)
      rotateAbout(tr,chestTree,axisAngle(Up,cy),position(tr[chest]));
    if(hy!=0.f)
      rotateAbout(tr,headTree, axisAngle(Up,hy),position(tr[head]));

    // up and down around the line through the shoulders, turned with the body
    const Vec3  f   = mul(axisAngle(Up,y),forward);
    const Vec3  lat = normalize(cross(f,Up));
    const float cp  = (headOnly || chest==size_t(-1)) ? 0.f : p*ChestPitch;
    const float hp  = p - cp;
    if(cp!=0.f)
      rotateAbout(tr,chestTree,axisAngle(lat,cp),position(tr[chest]));
    if(hp!=0.f)
      rotateAbout(tr,headTree, axisAngle(lat,hp),position(tr[head]));

    parts[0] = hy;
    parts[1] = cy;
    parts[2] = wy;
    }

  if(faceAxis>=0) {
    facing = axis(tr[head],faceAxis)*faceSign;
    const Vec3 d = normalize(target - position(tr[head]));
    if(weight>0.f && length(d)>0.5f)
      error = std::acos(std::clamp(dot(facing,d),-1.f,1.f))/Deg;
    }
  }

void Gaze::probe(Probe& p) const {
  p.valid     = valid;
  p.active    = weight>0.f;
  p.weight    = weight;
  p.yaw       = yaw/Deg;
  p.pitch     = pitch/Deg;
  p.yawGoal   = yawGoal/Deg;
  p.pitchGoal = pitchGoal/Deg;
  p.error     = error;
  p.headYaw   = parts[0]/Deg;
  p.chestYaw  = parts[1]/Deg;
  p.waistYaw  = parts[2]/Deg;
  p.head      = headPos;
  p.facing    = facing;
  p.target    = target;
  }
