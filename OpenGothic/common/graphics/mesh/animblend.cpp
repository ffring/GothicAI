#include "animblend.h"

#include "animmath.h"

#include <Tempest/Log>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>

using namespace Tempest;

namespace {

using Quat = zenkit::Quat;

constexpr uint64_t MaxGap   = 250;     // ms between two frames: longer, and the velocity is not known
constexpr float    MaxSpeed = 3000.f;  // cm/s of a bone in its parent
constexpr float    MaxTurn  = 40.f;    // rad/s
constexpr float    MinAngle = 1e-4f;   // radians: less is no offset
constexpr float    MinMove  = 0.01f;   // cm
constexpr float    MaxTurnOffset = 170.f*float(M_PI)/180.f; // going further out, a rotation would flip the short way round
constexpr float    OutPerSpeed   = 0.135f; // how much further out (of the offset) the curve goes per unit of outSpeed
constexpr float    SidePeak      = 0.197f; // the curve from zero goes aside up to this * v0 * t1

bool enabled = []() {
  const char* v = std::getenv("OPENGOTHIC_ANIBLEND");
  return v==nullptr || v[0]!='0';
  }();

AnimBlend::Params prm;

Vec3 vec(const zenkit::Vec3& v) {
  return Vec3(v.x,v.y,v.z);
  }

zenkit::Vec3 vec(const Vec3& v) {
  return zenkit::Vec3(v.x,v.y,v.z);
  }

float dot(const Vec3& a, const Vec3& b) {
  return a.x*b.x + a.y*b.y + a.z*b.z;
  }

Vec3 clampLength(const Vec3& v, float max) {
  const float l = v.length();
  return l>max ? v*(max/l) : v;
  }

Quat mul(const Quat& a, const Quat& b) {
  return Quat(a.w*b.w - a.x*b.x - a.y*b.y - a.z*b.z,
              a.w*b.x + a.x*b.w + a.y*b.z - a.z*b.y,
              a.w*b.y - a.x*b.z + a.y*b.w + a.z*b.x,
              a.w*b.z + a.x*b.y - a.y*b.x + a.z*b.w);
  }

Quat conj(const Quat& q) {
  return Quat(q.w,-q.x,-q.y,-q.z);
  }

Quat normalize(const Quat& q) {
  const float l = std::sqrt(q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z);
  return l>0 ? Quat(q.w/l,q.x/l,q.y/l,q.z/l) : Quat(1,0,0,0);
  }

// rotation vector (axis * angle) of a unit quaternion, the short way round
Vec3 rotVec(Quat q) {
  if(q.w<0)
    q = Quat(-q.w,-q.x,-q.y,-q.z);
  const float s = std::sqrt(q.x*q.x + q.y*q.y + q.z*q.z);
  if(s<1e-6f)
    return Vec3(q.x,q.y,q.z)*2.f;
  const float angle = 2.f*std::atan2(s,q.w);
  return Vec3(q.x,q.y,q.z)*(angle/s);
  }

Quat fromRotVec(const Vec3& v) {
  const float angle = v.length();
  if(angle<1e-6f)
    return normalize(Quat(1.f,v.x*0.5f,v.y*0.5f,v.z*0.5f));
  const float s = std::sin(angle*0.5f)/angle;
  return Quat(std::cos(angle*0.5f),v.x*s,v.y*s,v.z*s);
  }

Vec3 rotate(const Quat& q, const Vec3& v) {
  const Quat r = mul(mul(q,Quat(0,v.x,v.y,v.z)),conj(q));
  return Vec3(r.x,r.y,r.z);
  }

}

bool AnimBlend::isEnabled() {
  return enabled;
  }

void AnimBlend::setEnabled(bool e) {
  enabled = e;
  Log::i("aniblend: ", e ? "on" : "off");
  }

bool AnimBlend::setParam(std::string_view name, float v) {
  if(name=="min")
    prm.minTime = v;
  else if(name=="scale")
    prm.scale = v;
  else if(name=="max")
    prm.maxTime = v;
  else if(name=="turn")
    prm.turnSpeed = v;
  else if(name=="move")
    prm.moveSpeed = v;
  else if(name=="out")
    prm.outSpeed = v;
  else if(name=="side")
    prm.sideSpeed = v;
  else
    return false;
  return true;
  }

const AnimBlend::Params& AnimBlend::params() {
  return prm;
  }

void AnimBlend::Curve::init(float x, float v, float t, float vMax) {
  // x >= 0: a length or an angle. Velocity away from zero carries the bone further out first: kept
  // up to vMax. Fast toward zero ends sooner, so the curve does not overshoot zero
  x0 = x;
  t1 = t;
  v0 = std::min(v,vMax);
  if(v0<-1e-4f)
    t1 = std::min(t1,-5.f*x0/v0);
  if(t1<=1e-4f || (x0<=0 && v0<=0)) {
    t1 = 0;
    return;
    }
  const float t2 = t1*t1, t3 = t2*t1, t4 = t3*t1, t5 = t4*t1;
  a0 = std::max((-8.f*v0*t1 - 20.f*x0)/t2, 0.f);
  A  = -(a0*t2 + 6.f*v0*t1 + 12.f*x0)/(2.f*t5);
  B  =  (3.f*a0*t2 + 16.f*v0*t1 + 30.f*x0)/(2.f*t4);
  C  = -(3.f*a0*t2 + 12.f*v0*t1 + 20.f*x0)/(2.f*t3);
  }

float AnimBlend::Curve::at(float t) const {
  if(t>=t1)
    return 0;
  return ((((A*t + B)*t + C)*t + 0.5f*a0)*t + v0)*t + x0;
  }

void AnimBlend::reset(size_t numBones) {
  cur .assign(numBones,Driver());
  drv .assign(numBones,Driver());
  off .assign(numBones,Offset());
  last.assign(numBones,Sample());
  outTime  = 0;
  lastTime = 0;
  }

// What the bone switched from: the shown pose and how it moved, carried on to now, against the new
// animation and how that moves (from its key frames)
void AnimBlend::prepare(size_t i, const Sample& target, const Sample& shown, float dtShown, float dtNow) {
  const Driver& c = cur[i];
  const Driver& d = drv[i];

  Vec3 vel, turn;
  if(dtShown>0) {
    vel  = clampLength((vec(shown.position) - vec(last[i].position))*(1.f/dtShown), MaxSpeed);
    turn = clampLength(rotVec(mul(shown.rotation,conj(last[i].rotation)))*(1.f/dtShown), MaxTurn);
    }
  const Vec3 pos = vec(shown.position) + vel*dtNow;
  const Quat rot = normalize(mul(fromRotVec(turn*dtNow),shown.rotation));

  Vec3 tvel, tturn;
  if(c.a!=nullptr && c.b!=nullptr && c.rate>0) {
    tvel  = (vec(c.b->position) - vec(c.a->position))*c.rate;
    tturn = rotVec(mul(c.b->rotation,conj(c.a->rotation)))*c.rate;
    }

  Quat q = normalize(mul(rot,conj(target.rotation)));
  if(q.w<0)
    q = Quat(-q.w,-q.x,-q.y,-q.z);

  Switch sw;
  sw.bone  = i;
  sw.turn  = rotVec(q);
  sw.angle = sw.turn.length();
  // the offset turns as the shown pose minus the new animation (seen through the offset)
  sw.dturn = turn - rotate(q,tturn);
  sw.move  = pos - vec(target.position);
  sw.len   = sw.move.length();
  sw.dmove = vel - tvel;
  // the MDS: the new animation's blend in, the old one's blend out
  sw.time  = float(std::max(c.in,d.out))*0.001f*prm.scale;
  pend.push_back(sw);
  }

// The switches of this frame: one blend time for all, so that a limb stays one limb on the way (a
// time per bone bent the legs of a stumbling waran apart). A large offset takes longer, not to whip
void AnimBlend::begin(uint64_t now) {
  float time = prm.minTime;
  for(auto& sw:pend) {
    time = std::max(time,sw.time);
    if(prm.turnSpeed>0)
      time = std::max(time,1.875f*sw.angle/prm.turnSpeed);
    if(prm.moveSpeed>0)
      time = std::max(time,1.875f*sw.len/prm.moveSpeed);
    }
  time      = std::min(time,std::max(prm.maxTime,prm.minTime));
  lastBlend = std::max(lastBlend,time);

  // an offset and its velocity: a curve along it from its size, one across it from zero; a rotation
  // does not go out past MaxTurnOffset (limit), aside or along
  auto init = [&](Curve& along, Curve& across, const Vec3& dir, Vec3& side, const Vec3& v, float x0, float limit) {
    const float va    = dot(dir,v);
    float       out   = prm.outSpeed*x0/time;
    float       aside = prm.sideSpeed*x0/time;
    if(limit>0) {
      out   = std::min(out,  std::max(limit-x0,0.f)/OutPerSpeed/time);
      aside = std::min(aside,std::sqrt(std::max(limit*limit - x0*x0,0.f))/(SidePeak*time));
      }
    along.init(x0,va,time,out);
    const Vec3  vs = v - dir*va;
    const float s  = vs.length();
    if(s>1e-3f*std::max(x0,1.f)) {
      side = vs*(1.f/s);
      across.init(0,s,time,aside);
      }
    };

  for(auto& sw:pend) {
    Offset& o = off[sw.bone];
    o = Offset();
    o.start = now;
    if(sw.angle>MinAngle) {
      o.axis = sw.turn*(1.f/sw.angle);
      init(o.rot,o.rotSide,o.axis,o.axisSide,sw.dturn,sw.angle,MaxTurnOffset);
      }
    if(sw.len>MinMove) {
      o.dir = sw.move*(1.f/sw.len);
      init(o.pos,o.posSide,o.dir,o.dirSide,sw.dmove,sw.len,0);
      }
    o.end    = std::max(std::max(o.rot.t1,o.rotSide.t1),std::max(o.pos.t1,o.posSide.t1));
    o.active = o.end>0;
    if(log && o.active) {
      char buf[256];
      std::snprintf(buf,sizeof(buf),"aniblend: %llu bone %zu %s -> %s: turn %.1f deg v %.1f/%.1f side %.1f, move %.1f cm v %.0f, t %.3f",
                    (unsigned long long)now,sw.bone,drv[sw.bone].seq->name.c_str(),cur[sw.bone].seq->name.c_str(),
                    double(sw.angle*180.f/float(M_PI)),double(dot(o.axis,sw.dturn)),double(o.rot.v0),double(o.rotSide.v0),
                    double(sw.len),double(dot(o.dir,sw.dmove)),double(time));
      Log::d(buf);
      }
    }
  pend.clear();
  }

bool AnimBlend::apply(const Sample* raw, Sample* out, size_t numBones, uint64_t now) {
  numBones = std::min(numBones,cur.size());
  if(now<outTime) {
    // the time went back (a save loaded): nothing to blend from
    for(auto& o:off)
      o.active = false;
    outTime  = 0;
    lastTime = 0;
    }

  const bool  next    = (now!=outTime);
  const float dtShown = (lastTime>0 && outTime>lastTime && outTime-lastTime<=MaxGap) ? float(outTime-lastTime)*0.001f : 0.f;
  const float dtNow   = (outTime>0 && next) ? float(std::min(now-outTime,MaxGap))*0.001f : 0.f;

  // the switches first: they start from the pose shown, before it changes
  for(size_t i=0; i<numBones; ++i) {
    const Driver& c = cur[i];
    const Driver& d = drv[i];
    if(c.seq==nullptr || d.seq==nullptr || (c.seq==d.seq && c.sAnim==d.sAnim))
      continue;
    started++;
    if(enabled) {
      prepare(i,raw[i],out[i],dtShown,dtNow);
      continue;
      }
    Offset& o  = off[i];
    o          = Offset();
    o.fade     = true;
    o.from     = out[i];
    o.start    = now;
    o.fadeTime = float(std::max(c.in,c.out))*0.001f;
    o.active   = o.fadeTime>0;
    lastBlend  = std::max(lastBlend,o.fadeTime);
    }
  if(!pend.empty())
    begin(now);

  bool blending = false;
  for(size_t i=0; i<numBones; ++i) {
    const Driver& c = cur[i];
    if(c.seq==nullptr)
      continue;
    Driver&    d     = drv[i];
    const bool fresh = (d.seq==nullptr);
    d = c;

    Sample  res = raw[i];
    Offset& o   = off[i];
    if(o.active && o.fade) {
      const float t = float(now-o.start)*0.001f;
      if(t>=o.fadeTime) {
        o.active = false;
        } else {
        res      = mix(o.from,raw[i],t/o.fadeTime);
        blending = true;
        }
      }
    else if(o.active) {
      const float t = float(now-o.start)*0.001f;
      if(t>=o.end) {
        o.active = false;
        } else {
        if(t<o.rot.t1 || t<o.rotSide.t1)
          res.rotation = normalize(mul(fromRotVec(o.axis*o.rot.at(t) + o.axisSide*o.rotSide.at(t)),raw[i].rotation));
        if(t<o.pos.t1 || t<o.posSide.t1)
          res.position = vec(vec(raw[i].position) + o.dir*o.pos.at(t) + o.dirSide*o.posSide.at(t));
        blending = true;
        }
      }
    if(next)
      last[i] = fresh ? res : out[i];
    out[i] = res;
    }

  if(next) {
    lastTime = outTime;
    outTime  = now;
    }
  return blending;
  }

void AnimBlend::probe(Probe& p) {
  p.valid   = !cur.empty();
  p.active  = 0;
  p.angle   = 0;
  p.started = started;
  p.time    = lastBlend;
  started   = 0;
  lastBlend = 0;
  const float t = float(outTime)*0.001f;
  for(auto& o:off) {
    if(!o.active || o.fade)
      continue;
    p.active++;
    p.angle = std::max(p.angle,o.rot.at(t - float(o.start)*0.001f)*180.f/float(M_PI));
    }
  }
