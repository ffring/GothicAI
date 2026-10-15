#include "aimwalk.h"

#include <Tempest/Log>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string_view>

#include "animmath.h"
#include "ikmath.h"
#include "skeleton.h"

using namespace Tempest;
using namespace IkMath;

namespace {

constexpr float BlendIn  = 0.25f; // seconds: the legs take the steps...
constexpr float BlendOut = 0.3f;  // ...and stand again
constexpr float StopBlendOut = 0.2f; // seconds: the body is already stopped at an edge
constexpr float StopFootSpeed = 150.f; // cm/s: return to the stance with the body stopped
constexpr float StopFootFrame = 5.f;   // leave room below the final 6 cm/frame ankle limit

bool enabled = []() {
  const char* v = std::getenv("OPENGOTHIC_AIMWALK");
  return v==nullptr || v[0]!='0';
  }();

float wrapAngle(float a) {
  return std::remainder(a,2.f*float(M_PI));
  }

// about up: the yaw (atan2 of z, x) of what it turns grows by a
Mat3 turnY(float a) {
  return axisAngle(Up,-a);
  }

}

AimWalk::Probe AimWalk::playerProbe;

bool AimWalk::isEnabled() {
  return enabled;
  }

void AimWalk::setEnabled(bool e) {
  enabled = e;
  Log::i("aimwalk: ", e ? "on" : "off");
  }

void AimWalk::setSkeleton(const Skeleton* sk) {
  *this = AimWalk();
  skeleton = sk;
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
  auto  depth = [&](size_t id) {
    size_t d = 0;
    for(size_t i=nodes[id].parent; i<nodes.size(); i=nodes[i].parent)
      ++d;
    return d;
    };
  auto  tree = [&](size_t root, std::vector<size_t>& out) {
    for(size_t i=0; i<nodes.size(); ++i)
      if(isUnder(i,root))
        out.push_back(i);
    std::stable_sort(out.begin(),out.end(),[&](size_t a, size_t b){ return depth(a)<depth(b); });
    };

  root   = find("BIP01");
  pelvis = find("BIP01 PELVIS");
  spine  = find("BIP01 SPINE");
  spine1 = find("BIP01 SPINE1");
  spine2 = find("BIP01 SPINE2");
  lThigh = find("BIP01 L THIGH");
  rThigh = find("BIP01 R THIGH");
  lArm   = find("BIP01 L UPPERARM");
  rArm   = find("BIP01 R UPPERARM");
  lFoot  = find("BIP01 L FOOT");
  rFoot  = find("BIP01 R FOOT");
  for(auto i:{root,pelvis,spine,spine1,spine2,lThigh,rThigh,lArm,rArm,lFoot,rFoot})
    if(i>=nodes.size())
      return;
  if(nodes[pelvis].parent!=root || nodes[spine].parent!=pelvis || nodes[spine1].parent!=spine ||
     nodes[spine2].parent!=spine1 || nodes[lThigh].parent!=pelvis || nodes[rThigh].parent!=pelvis ||
     !isUnder(lFoot,lThigh) || !isUnder(rFoot,rThigh))
    return;
  tree(lThigh,legsTree);
  tree(rThigh,legsTree);
  std::stable_sort(legsTree.begin(),legsTree.end(),[&](size_t a, size_t b){ return depth(a)<depth(b); });
  tree(spine, spineTree);
  tree(spine1,spine1Tree);
  tree(spine2,spine2Tree);
  valid = true;
  }

float AimWalk::cycle() const {
  // the loop as its root goes: the last frame is the first one again
  if(seq==nullptr || seq->data==nullptr || seq->data->numFrames<2 || seq->data->fpsRate<=0.f)
    return 0;
  return float(seq->data->numFrames-1)*1000.f/seq->data->fpsRate;
  }

float AimWalk::rate() const {
  const float c = cycle();
  if(c<=0.f)
    return 0;
  const float v = std::sqrt(seq->data->moveTr.x*seq->data->moveTr.x + seq->data->moveTr.z*seq->data->moveTr.z)/c; // cm/ms
  if(v<=0.f)
    return 0;
  return (dir>0.f ? Forward : Back)/1000.f/v;
  }

bool AimWalk::sample(float at, size_t node, zenkit::AnimationSample& out) const {
  if(node>=seqNode.size() || seqNode[node]<0)
    return false;
  const auto&  d  = *seq->data;
  const size_t n  = d.nodeIndex.size();
  const float  fr = at*d.fpsRate/1000.f;
  uint32_t     a  = uint32_t(fr);
  const float  k  = fr-float(a);
  a = std::min(a,d.numFrames-2);
  const uint32_t b = a+1;
  out = mix(d.samples[size_t(a)*n+size_t(seqNode[node])],d.samples[size_t(b)*n+size_t(seqNode[node])],k);
  return true;
  }

float AimWalk::hipsYaw(const Matrix4x4* tr) const {
  const Vec3 d = position(tr[lThigh]) - position(tr[rThigh]);
  return std::atan2(d.z,d.x);
  }

float AimWalk::chestYaw(const Matrix4x4* tr) const {
  const Vec3 d = position(tr[lArm]) - position(tr[rArm]);
  return std::atan2(d.z,d.x);
  }

bool AimWalk::update(const Input& in) {
  if(!valid)
    return false;
  if(in.legs!=seq) {
    seq = in.legs;
    seqNode.assign(skeleton->nodes.size(),-1);
    if(seq!=nullptr && seq->data!=nullptr) {
      auto& d = *seq->data;
      for(size_t i=0; i<d.nodeIndex.size(); ++i)
        if(d.nodeIndex[i]<seqNode.size())
          seqNode[d.nodeIndex[i]] = int(i);
      if(d.numFrames<2 || d.samples.size()<size_t(d.numFrames)*d.nodeIndex.size() || seqNode[root]<0 || seqNode[pelvis]<0)
        seq = nullptr;
      }
    }

  const float dt   = float(in.dt)/1000.f;
  const float prev = weight;
  walk = std::clamp(in.walk,-1.f,1.f);
  // Keep the edge stop while its legs settle even if the caller now asks for no movement.
  const bool wasBlocked = blocked;
  blocked = in.blocked || (blocked && walk==0.f && weight>0.f);
  settle = false;
  const bool on = enabled && seq!=nullptr && walk!=0.f && !blocked && cycle()>0.f;
  if(on) {
    if(weight<=0.f)
      phase = 0;
    dir    = walk>0.f ? 1.f : -1.f;
    weight = std::min(1.f, weight + dt/BlendIn);
    } else if(blocked && weight>0.f) {
    // apply knows the actual stance and can bound the ankles' return, rather than guessing from weight.
    settle = true;
    // The contact frame may still contain the body's last clipped step; start returning next frame.
    settleDt = wasBlocked ? dt : 0.f;
    } else {
    weight = std::max(0.f, weight - dt/BlendOut);
    }
  if(seq==nullptr)
    weight = 0;
  if(weight>0.f && !blocked) {
    // the steps go on as they are while they fade out
    const float c = cycle();
    phase = std::fmod(phase + dir*rate()*float(in.dt), c);
    if(phase<0.f)
      phase += c;
    }
  return weight>0.f || prev>0.f;
  }

Vec3 AimWalk::move(uint64_t dt) const {
  if(!isActive() || blocked)
    return Vec3();
  const auto& d = *seq->data;
  const float c = cycle();
  if(c<=0.f || d.tr.empty())
    return Vec3();
  // the root's way: as Animation::Sequence::translateXZ, in fractions of a ms
  auto at = [&](float ms) {
    const float  fr = ms*d.fpsRate/1000.f;
    const size_t n  = d.tr.size();
    const size_t fa = size_t(fr);
    const float  k  = fr-float(fa);
    Vec3 a = d.tr[fa%n]     + d.moveTr*float(fa/n);
    Vec3 b = d.tr[(fa+1)%n] + d.moveTr*float((fa+1)/n);
    return a + (b-a)*k;
    };
  const float span = rate()*float(dt);
  Vec3 r;
  if(dir>0.f) {
    r = at(phase+span) - at(phase);
    } else {
    float from = phase-span;
    while(from<0.f)
      from += c;
    r = at(from) - at(from+span);
    }
  return Vec3(r.x*weight,0,r.z*weight);
  }

void AimWalk::apply(Matrix4x4* tr, size_t numBones, const zenkit::AnimationSample* base, const Matrix4x4& m) {
  twist = chest = drop = 0;
  if(!valid || !isActive() || base==nullptr || numBones!=skeleton->nodes.size())
    return;
  auto& nodes = skeleton->nodes;

  // the stance as the aim holds it
  stance.assign(tr,tr+numBones);
  const float hipsA  = hipsYaw(tr);
  const float chestA = chestYaw(tr);

  // the legs step: their animation over the stance's, as far as they are in; the root where the stance has
  // it on the ground (the model goes where the steps take it, Pose::animMoveSpeed), its height and turn
  // the steps'
  auto local = [&](size_t i) {
    zenkit::AnimationSample s;
    if(!sample(phase,i,s))
      return Matrix4x4();
    s = mix(base[i],s,weight);
    if(i==root) {
      s.position.x = base[i].position.x;
      s.position.z = base[i].position.z;
      }
    return mkMatrix(s);
    };
  auto set = [&](size_t i) {
    const size_t p = nodes[i].parent;
    if(i<seqNode.size() && seqNode[i]>=0) {
      tr[i] = (p<numBones ? tr[p] : m)*local(i);
      } else {
      // not stepped: as it hangs in the stance
      Matrix4x4 inv = p<numBones ? stance[p] : m;
      inv.inverse();
      tr[i] = (p<numBones ? tr[p] : m)*(inv*stance[i]);
      }
    };
  auto build = [&]() {
    set(root);
    set(pelvis);
    for(auto i:legsTree)
      set(i);
    };
  if(settle) {
    // Pose can rebuild several times in a frame for the other post-passes. Advance the fade only once.
    settle = false;
    const float from = weight;
    const float to   = std::max(0.f,from-settleDt/StopBlendOut);
    const float step = std::min(StopFootFrame,StopFootSpeed*settleDt);
    build();
    const Vec3 left  = position(tr[lFoot]);
    const Vec3 right = position(tr[rFoot]);
    auto travel = [&]() {
      if(weight<=0.f)
        return std::max(length(position(stance[lFoot])-left),length(position(stance[rFoot])-right));
      build();
      return std::max(length(position(tr[lFoot])-left),length(position(tr[rFoot])-right));
      };
    weight = to;
    if(travel()>step) {
      float low = to, high = from;
      for(int i=0; i<8; ++i) {
        weight = (low+high)*0.5f;
        if(travel()>step)
          low = weight;
        else
          high = weight;
        }
      weight = high;
      }
    if(weight<=0.f) {
      std::copy(stance.begin(),stance.end(),tr);
      return;
      }
    }
  build();

  // the upper body goes with the hips, its turn kept: the hips' turn is spread over the spine (the lower
  // spine takes most of it, the chest none)
  const Vec3 shift = position(tr[pelvis]) - position(stance[pelvis]);
  for(auto i:spineTree) {
    tr[i].set(3,0,tr[i].at(3,0)+shift.x);
    tr[i].set(3,1,tr[i].at(3,1)+shift.y);
    tr[i].set(3,2,tr[i].at(3,2)+shift.z);
    }
  const float d = wrapAngle(hipsYaw(tr)-hipsA);
  rotateAbout(tr,spineTree, turnY( d*2.f/3.f),position(tr[spine]));
  rotateAbout(tr,spine1Tree,turnY(-d/3.f),    position(tr[spine1]));
  rotateAbout(tr,spine2Tree,turnY(-d/3.f),    position(tr[spine2]));

  twist = d*180.f/float(M_PI);
  chest = wrapAngle(chestYaw(tr)-chestA)*180.f/float(M_PI);
  drop  = -shift.y;
  }

void AimWalk::probe(Probe& p) const {
  p.valid  = valid;
  p.weight = weight;
  p.walk   = walk;
  const float c = cycle();
  p.phase  = c>0.f ? phase/c : 0.f;
  p.speed  = 0;
  if(isActive()) {
    const Vec3 v = move(100);
    p.speed = std::sqrt(v.x*v.x+v.z*v.z)*10.f*dir;
    }
  p.twist  = twist;
  p.chest  = chest;
  p.drop   = drop;
  }
