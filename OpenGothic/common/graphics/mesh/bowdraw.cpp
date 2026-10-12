#include "bowdraw.h"

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

constexpr float Brace    = 17.f;  // cm: the string at rest from the grip (a bow without a morph)
constexpr float MaxSpan  = 80.f;  // cm: a longer draw is not a bow held in the aim (the pose is off)
constexpr float Reach    = 0.98f; // of the arm's length: it does not straighten further...
constexpr float Comfort  = 0.92f; // ...nor reaches for the string further: a string out of reach rests
                                  // drawn a bit, the draw starts from there
constexpr float BlendIn  = 0.15f; // seconds: the hand goes to the string...
constexpr float BlendOut = 0.15f; // ...and back to the animation
constexpr float LetDown  = 3.f;   // 1/s: the string let down without a shot goes back that fast
constexpr float MinShot  = 0.3f;  // drawn that far the string let go is a shot (Archery::MinDraw)
constexpr float Follow   = 6.f;   // cm the hand flies back past the face as the string goes...
constexpr float FollowT  = 0.3f;  // ...for that long, there and back (smooth at both ends)

bool enabled = []() {
  const char* v = std::getenv("OPENGOTHIC_BOWDRAW");
  return v==nullptr || v[0]!='0';
  }();

}

BowDraw::Probe BowDraw::playerProbe;

bool BowDraw::isEnabled() {
  return enabled;
  }

void BowDraw::setEnabled(bool e) {
  enabled = e;
  Log::i("bowdraw: ", e ? "on" : "off");
  }

void BowDraw::setSkeleton(const Skeleton* sk) {
  *this = BowDraw();
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
    for(size_t i=0; i<nodes.size(); ++i)
      if(isUnder(i,root))
        out.push_back(i);
    };

  upper   = find("BIP01 R UPPERARM");
  fore    = find("BIP01 R FOREARM");
  hand    = find("BIP01 R HAND");
  slot    = find("ZS_RIGHTHAND"); // the arrow sits there
  bowHand = find("ZS_LEFTHAND");  // the bow
  if(bowHand==size_t(-1))
    bowHand = find("BIP01 L HAND");
  if(upper==size_t(-1) || fore==size_t(-1) || hand==size_t(-1) || slot==size_t(-1) || bowHand==size_t(-1))
    return;
  if(!isUnder(fore,upper) || !isUnder(hand,fore) || !isUnder(slot,hand))
    return;
  tree(upper,upperTree);
  tree(fore,foreTree);
  tree(hand,handTree);
  valid = true;
  }

bool BowDraw::update(const Input& in) {
  if(!valid)
    return false;
  const float dt   = float(in.dt)/1000.f;
  const bool  on   = in.active && in.ready && enabled;
  const float prev     = weight;
  const float prevDraw = draw;
  const float prevBack = follow;
  given = in.span;
  // the shot: the string held drawn goes (not held still any more, not let down): the hand follows through
  if(in.active && wasReady && !in.ready && draw>=MinShot && weight>0.f)
    followT = 0;
  wasReady = in.active && in.ready;
  followT  = std::min(FollowT, followT+dt);
  const float bump = std::sin(float(M_PI)*followT/FollowT);
  follow   = followT<FollowT ? Follow*bump*bump : 0.f;
  if(on) {
    weight = std::min(1.f, weight + dt/BlendIn);
    // drawn as it is; let down (no shot) back a bit slower
    draw   = in.draw>=draw ? in.draw : std::max(in.draw, draw - dt*LetDown);
    }
  else if(in.active && in.nock>=0.f && draw<=0.f) {
    // the next arrow comes to the string: the hand brings it there, not to the face, as the reload ends
    weight = std::min(in.nock, weight + dt/BlendIn);
    }
  else {
    // not holding still (the shot, the next arrow): the hand as it was goes back to the animation; then
    // the string is shown at rest
    weight = std::max(0.f, weight - dt/BlendOut);
    if(weight<=0.f && in.active && in.nock>=0.f)
      draw = 0;
    }
  return weight!=prev || (weight>0.f && draw!=prevDraw) || follow!=prevBack;
  }

void BowDraw::apply(Matrix4x4* tr, size_t numBones) {
  travel = 0;
  miss   = 0;
  if(!valid || upperTree.back()>=numBones || foreTree.back()>=numBones || handTree.back()>=numBones ||
     slot>=numBones || bowHand>=numBones)
    return;

  // the arrow along -X of the slot, the nock at it; drawn fully by the animation
  const Vec3 nock = position(tr[slot]);
  const Vec3 axis = normalize(Vec3(-tr[slot].at(0,0),-tr[slot].at(0,1),-tr[slot].at(0,2)));
  const Vec3  grip  = position(tr[bowHand]);
  const float ahead = dot(grip-nock,axis);
  span = given>0.f ? given : ahead-Brace;
  if(ahead<=0.f || span<=0.f || span>MaxSpan) {
    // the bow is not ahead of the arrow: not the aim pose
    span = 0;
    return;
    }
  if(weight<=0.f && follow<=0.f)
    return;

  // forward along the arrow, as far as the arm reaches: |hand + axis*t - shoulder| <= Comfort*arm
  const Vec3  h0    = position(tr[hand]);
  const Vec3  p     = h0 - position(tr[upper]);
  const float arm   = Comfort*(length(position(tr[fore])-position(tr[upper])) + length(h0-position(tr[fore])));
  const float pa    = dot(p,axis);
  const float disc  = pa*pa - dot(p,p) + arm*arm;
  const float reach = disc>0.f ? std::max(0.f, -pa + std::sqrt(disc)) : 0.f;
  // the whole draw over what the arm reaches: the hand moves all the way, the string with it
  travel = std::min(span,reach)*(1.f-draw)*weight;
  // the follow through: back past the face
  const float move = travel - follow;
  if(std::abs(move)<0.01f)
    return;
  const Vec3 target = h0 + axis*move;
  // the elbow bends in the plane it is animated in
  const Vec3 bend   = position(tr[fore]) - position(tr[upper]);
  TwoBone    tb;
  if(!twoBone(tr,upper,fore,hand,upperTree,foreTree,target,bend,Reach,0.f,tb))
    return;
  // the hand keeps its turn: the arrow keeps its line
  rotateAbout(tr,handTree,transpose(mul(tb.r1,tb.r0)),position(tr[hand]));
  miss = length(position(tr[hand])-target);
  }

void BowDraw::probe(Probe& p) const {
  p.valid  = valid;
  p.weight = weight;
  p.draw   = draw;
  p.span   = span;
  p.travel = travel;
  p.miss   = miss;
  p.follow = follow;
  }
