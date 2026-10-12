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
constexpr float HeadR    = 10.f;  // cm: the head with the hair, a capsule from the neck to the crown that thick
constexpr float RoundOut = 15.f;  // cm from the head's axis at least: the hand goes round it, on its side
constexpr float NockBack = 36.f;  // cm the nock is behind the slot the arrow sits in (G2)...
constexpr float TipAhead = 40.f;  // ...the tip ahead of it
constexpr float Follow   = 4.f;   // cm the hand flies back past the face as the string goes...
constexpr float FollowT  = 0.3f;  // ...for that long, there and back (smooth at both ends)

bool enabled = []() {
  const char* v = std::getenv("OPENGOTHIC_BOWDRAW");
  return v==nullptr || v[0]!='0';
  }();

float smooth(float x) {
  x = std::clamp(x,0.f,1.f);
  return x*x*(3.f-2.f*x);
  }

// From a to b round an axis (c, unit u), turning through e1's side of it (e1, e2: across the axis): the
// angle about the axis, the height along it and the distance from it go evenly; the distance is not less
// than r on the way
Vec3 roundAxis(const Vec3& a, const Vec3& b, const Vec3& c, const Vec3& u, const Vec3& e1, const Vec3& e2,
               float r, float s) {
  auto polar = [&](const Vec3& p, float& h, float& d, float& an) {
    const Vec3 rel = p-c;
    h  = dot(rel,u);
    const Vec3 rho = rel-u*h;
    d  = length(rho);
    an = std::atan2(dot(rho,e2),dot(rho,e1));
    };
  float ha, da, aa, hb, db, ab;
  polar(a,ha,da,aa);
  polar(b,hb,db,ab);
  const float h  = ha + (hb-ha)*s;
  const float an = aa + (ab-aa)*s;
  float       d  = da + (db-da)*s;
  // away from the axis on the way (not at the ends: it starts at the cheek)
  d = d + std::max(0.f,r-d)*std::sin(float(M_PI)*s);
  return c + u*h + e1*(d*std::cos(an)) + e2*(d*std::sin(an));
  }

// cm between segment a-b and segment c-d
float segDist(const Vec3& a, const Vec3& b, const Vec3& c, const Vec3& d) {
  // sampled: short segments, a few cm is enough for the bench
  float best = 1e9f;
  for(int i=0; i<=16; ++i) {
    const Vec3  p  = a + (b-a)*(float(i)/16.f);
    const Vec3  cd = d-c;
    const float l  = dot(cd,cd);
    const float t  = l>0.f ? std::clamp(dot(p-c,cd)/l,0.f,1.f) : 0.f;
    best = std::min(best,length(c+cd*t-p));
    }
  return best;
  }

Vec3 arrowAxis(const Matrix4x4& slot) {
  return normalize(Vec3(-slot.at(0,0),-slot.at(0,1),-slot.at(0,2)));
  }

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
  head    = find("BIP01 HEAD");
  crown   = find("ZS_HELMET");
  chest   = find("BIP01 SPINE2");
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

  // the upper body to hold in the reload, the drawing arm in it; the hand goes round the head
  const size_t spine = find("BIP01 SPINE");
  if(spine==size_t(-1) || chest==size_t(-1) || head==size_t(-1) || crown==size_t(-1) ||
     !isUnder(hand,chest) || !isUnder(bowHand,spine) || !isUnder(chest,spine))
    return;
  bodyRoot = nodes[spine].parent;
  if(bodyRoot>=nodes.size())
    return;
  tree(spine,bodyTree);
  heldRel.resize(nodes.size());
  }

bool BowDraw::grabNodes(size_t& c, size_t& h, size_t& e) const {
  if(bodyTree.empty())
    return false;
  c = chest;
  h = hand;
  e = fore;
  return true;
  }

bool BowDraw::update(const Input& in) {
  if(!valid)
    return false;
  const float dt   = float(in.dt)/1000.f;
  const bool  on   = in.active && in.ready && enabled;
  const float prev     = weight;
  const float prevDraw = draw;
  const float prevBack = follow;
  active     = in.active;
  inHand     = in.arrow;
  given      = in.span;
  hasString  = in.hasString;
  stringRest = in.stringRest;
  stringPull = in.stringPull;
  if(in.grabKnown) {
    grabKnown = true;
    grabHand  = in.grabHand;
    grabElbow = in.grabElbow;
    }
  // the aim pose is taken while the bow is held still; the reload holds it
  capture  = in.active && in.ready && enabled && !bodyTree.empty();
  holding  = in.active && in.reload>=0.f && enabled && held && !bodyTree.empty();
  // the quiver reached at GrabAt, or later, quick hands no faster than MinReach
  const float grab = in.reloadTime>0.f ? std::clamp(MinReach/in.reloadTime,GrabAt,0.4f) : GrabAt;
  const float nock = std::max(NockAt,grab+0.3f);
  toQuiver = holding ? smooth(in.reload/grab) : 0.f;
  toString = holding ? smooth((in.reload-grab)/(nock-grab)) : 0.f;
  // the shot: the string held drawn goes (not held still any more, not let down): the hand follows through
  if(in.active && wasReady && !in.ready && draw>=MinShot && weight>0.f) {
    followT = 0;
    shotAt  = travel;
    }
  wasReady = in.active && in.ready;
  followT  = std::min(FollowT, followT+dt);
  const float bump = std::sin(float(M_PI)*followT/FollowT);
  follow   = followT<FollowT ? Follow*bump*bump : 0.f;
  if(on) {
    weight = std::min(1.f, weight + dt/BlendIn);
    // drawn as it is; let down (no shot) back a bit slower
    draw   = in.draw>=draw ? in.draw : std::max(in.draw, draw - dt*LetDown);
    }
  else if(holding) {
    // the reload: the hand goes for the next arrow and brings it to the string (not drawn)
    weight = toString;
    draw   = 0;
    }
  else {
    // not holding still: the hand as it was goes back to the animation
    weight = std::max(0.f, weight - dt/BlendOut);
    }
  return weight!=prev || (weight>0.f && draw!=prevDraw) || follow!=prevBack || holding;
  }

void BowDraw::hold(Matrix4x4* tr, size_t numBones, const Matrix4x4& object) {
  if(!valid || bodyTree.empty() || bodyTree.back()>=numBones || bodyRoot>=numBones)
    return;
  // the frame the upper body is held in: turned as the body faces, at the pelvis
  const Matrix4x4 frame = compose(rotationOf(object),position(tr[bodyRoot]));
  frameNow = frame;
  if(holding) {
    for(auto i:bodyTree)
      tr[i] = frame*heldRel[i];
    return;
    }
  if(capture) {
    // the aim pose as animated
    Matrix4x4 inv = frame;
    inv.inverse();
    for(auto i:bodyTree)
      heldRel[i] = inv*tr[i];
    held = true;
    }
  }

void BowDraw::measureHead(const Matrix4x4* tr, size_t numBones) {
  gapHand = gapFore = gapArrow = 99;
  if(head>=numBones || hand>=numBones || fore>=numBones || slot>=numBones)
    return;
  const Vec3 h0 = position(tr[head]);
  const Vec3 h1 = crown<numBones ? position(tr[crown]) : h0+Vec3(0,20,0);
  const Vec3 ax = arrowAxis(tr[slot]);
  const Vec3 sl = position(tr[slot]);
  gapHand  = segDist(position(tr[hand]),position(tr[hand]),h0,h1) - HeadR;
  gapFore  = segDist(position(tr[fore])*0.5f+position(tr[hand])*0.5f,position(tr[hand]),h0,h1) - HeadR;
  gapArrow = inHand ? segDist(sl-ax*NockBack,sl+ax*TipAhead,h0,h1) - HeadR : 99.f;
  }

void BowDraw::apply(Matrix4x4* tr, size_t numBones) {
  implApply(tr,numBones);
  measureHead(tr,numBones);
  if(hand<numBones) {
    Matrix4x4 inv = frameNow;
    inv.inverse();
    handAt = position(inv*tr[hand]);
    }
  }

bool BowDraw::moveHand(Matrix4x4* tr, const Vec3& target, const Vec3* bendTo) {
  // the elbow bends in the plane it is animated in, or toward where it is asked to
  const Vec3 bend = bendTo!=nullptr ? *bendTo : position(tr[fore]) - position(tr[upper]);
  TwoBone    tb;
  if(!twoBone(tr,upper,fore,hand,upperTree,foreTree,target,bend,Reach,0.f,tb))
    return false;
  // the hand keeps its turn: the arrow keeps its line
  rotateAbout(tr,handTree,transpose(mul(tb.r1,tb.r0)),position(tr[hand]));
  return true;
  }

void BowDraw::reload(Matrix4x4* tr, size_t numBones, const Vec3& nock, const Vec3& axis) {
  // the hand from the aim (the upper body is held in it) round the head on its side to the quiver, where
  // the animation takes the arrow, then round the head again to the string
  if(chest>=numBones || head>=numBones || crown>=numBones)
    return;
  const Vec3 hA = position(tr[hand]);
  const Mat3 rA = rotationOf(tr[hand]);
  const Vec3 eA = position(tr[fore])-position(tr[upper]);
  Vec3 hB = hA, eB = eA;
  Mat3 rB = rA;
  if(grabKnown) {
    const Matrix4x4 g = tr[chest]*grabHand;
    hB = position(g);
    rB = rotationOf(g);
    eB = position(tr[chest]*grabElbow)-position(tr[upper]);
    }
  // round the head's axis on the side of the drawing shoulder
  const Vec3  h0   = position(tr[head]);
  const Vec3  hx   = position(tr[crown])-h0;
  const float hl   = dot(hx,hx);
  if(hl<=0.f)
    return;
  const Vec3  u    = hx*(1.f/std::sqrt(hl));
  const Vec3  c    = h0 + hx*0.5f;
  const Vec3  sh   = position(tr[upper])-c;
  const Vec3  e1   = normalize(sh - u*dot(sh,u));
  const Vec3  e2   = cross(u,e1);
  if(length(e1)<0.5f)
    return;

  Vec3  at;
  Mat3  turn;
  Vec3  elbow;
  float s;
  if(toString<=0.f) {
    s     = toQuiver;
    at    = roundAxis(hA,hB,c,u,e1,e2,RoundOut,s);
    turn  = toMat3(slerp(toQuat(rA),toQuat(rB),s));
    elbow = eA + (eB-eA)*s;
    // a short draw let go: the hand from where it was
    at   += (nock-hA)*(shotAt/std::max(length(nock-hA),1.f))*(1.f-s);
    } else {
    s     = toString;
    at    = roundAxis(hB,nock,c,u,e1,e2,RoundOut,s);
    turn  = toMat3(slerp(toQuat(rB),toQuat(rA),s));
    elbow = eB + (eA-eB)*s;
    }
  // the follow through: back past the face
  at -= axis*follow;
  if(!moveHand(tr,at,&elbow))
    return;
  rotateAbout(tr,handTree,mul(turn,transpose(rotationOf(tr[hand]))),position(tr[hand]));
  miss = length(position(tr[hand])-at);
  }

void BowDraw::implApply(Matrix4x4* tr, size_t numBones) {
  travel = 0;
  miss   = 0;
  shown  = 1;
  if(!valid || upperTree.back()>=numBones || foreTree.back()>=numBones || handTree.back()>=numBones ||
     slot>=numBones || bowHand>=numBones)
    return;

  // the arrow along -X of the slot (the slot is on the shaft, the nock behind it); drawn fully by the
  // animation
  const Vec3  slotAt = position(tr[slot]);
  const Vec3  axis   = arrowAxis(tr[slot]);
  const Vec3  grip   = position(tr[bowHand]);
  const float ahead  = dot(grip-slotAt,axis);
  // the way the nock goes as the string is let down, the full draw of the animation to the string at rest:
  // the string's middle (its morph: G2 holds the arrow above the bow's middle, the string is pulled up to
  // it), else straight forward along the arrow
  Vec3 way = axis;
  fit = offset = angle = -1;
  if(hasString) {
    Vec3 rest = stringRest, pull = stringPull;
    tr[bowHand].project(rest);
    pull = Vec3(tr[bowHand].at(0,0)*pull.x + tr[bowHand].at(1,0)*pull.y + tr[bowHand].at(2,0)*pull.z,
                tr[bowHand].at(0,1)*pull.x + tr[bowHand].at(1,1)*pull.y + tr[bowHand].at(2,1)*pull.z,
                tr[bowHand].at(0,2)*pull.x + tr[bowHand].at(1,2)*pull.y + tr[bowHand].at(2,2)*pull.z);
    fit     = length(cross(rest+pull-slotAt,axis));
    offset  = length(cross(rest-slotAt,axis));
    angle   = std::acos(std::clamp(dot(normalize(pull)*-1.f,axis),-1.f,1.f))*180.f/float(M_PI);
    span    = length(pull);
    if(span>0.f)
      way = pull*(-1.f/span);
    } else {
    span = given>0.f ? given : ahead-Brace;
    }
  if(ahead<=0.f || span<=0.f || span>MaxSpan) {
    // the bow is not ahead of the arrow: not the aim pose
    span = 0;
    return;
    }

  // that way as far as the arm reaches: |hand + way*t - shoulder| <= Comfort*arm
  const Vec3  h0    = position(tr[hand]);
  const Vec3  p     = h0 - position(tr[upper]);
  const float arm   = Comfort*(length(position(tr[fore])-position(tr[upper])) + length(h0-position(tr[fore])));
  const float pw    = dot(p,way);
  const float disc  = pw*pw - dot(p,p) + arm*arm;
  const float reach = disc>0.f ? std::max(0.f, -pw + std::sqrt(disc)) : 0.f;
  const float full  = std::min(span,reach);

  if(holding) {
    // the reload: the string rests till the nock is at it, then shows drawn as far as it is (a little)
    shown = (1.f - full/span)*smooth((toString-0.8f)/0.2f);
    reload(tr,numBones,h0+way*full,axis);
    return;
    }
  if(weight<=0.f && follow<=0.f)
    return;

  // the whole draw over what the arm reaches: the hand moves all the way, the string with it
  travel = full*(1.f-draw)*weight;
  shown  = 1.f - travel/span;
  // the follow through: back past the face
  const Vec3 move = way*travel - axis*follow;
  if(length(move)<0.01f)
    return;
  const Vec3 target = h0 + move;
  if(!moveHand(tr,target))
    return;
  miss = length(position(tr[hand])-target);
  }

void BowDraw::probe(Probe& p) const {
  p.valid   = valid;
  p.weight  = weight;
  p.draw    = draw;
  p.span    = span;
  p.travel  = travel;
  p.miss    = miss;
  p.follow  = follow;
  p.fit     = fit;
  p.offset  = offset;
  p.angle   = angle;
  p.holding = holding;
  p.nock    = toString>0.f ? 1.f+toString : toQuiver;
  p.handAt  = handAt;
  p.head    = gapHand;
  p.fore    = gapFore;
  p.arrow   = gapArrow;
  }
