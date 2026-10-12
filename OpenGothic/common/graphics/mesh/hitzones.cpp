#include "hitzones.h"

#include <algorithm>
#include <cmath>
#include <cstdio>

#include "ikmath.h"
#include "skeleton.h"

using namespace Tempest;
using namespace IkMath;

namespace {

constexpr float RefSize = 160.f; // cm: a human from the toes to the head bone; the radii below are for it

struct Kind final {
  HitZones::Zone zone;
  float          r;     // cm, for RefSize
  };

bool has(std::string_view name, std::string_view part) {
  return name.find(part)!=std::string_view::npos;
  }

bool isSlot(std::string_view name) {
  return name.substr(0,3)=="ZS_";
  }

Kind kindOf(std::string_view n) {
  if(has(n,"HEAD"))
    return {HitZones::Head,11};
  if(has(n,"KIEFER") || has(n,"JAW"))
    return {HitZones::Head,8};
  if(has(n,"NECK"))
    return {HitZones::Body,7};
  if(has(n,"CLAVICLE"))
    return {HitZones::Body,8};
  if(has(n,"UPPERARM"))
    return {HitZones::Limb,6};
  if(has(n,"FOREARM"))
    return {HitZones::Limb,5};
  if(has(n,"FINGER"))
    return {HitZones::Limb,3};
  if(has(n,"HAND"))
    return {HitZones::Limb,4};
  if(has(n,"THIGH"))
    return {HitZones::Limb,8};
  if(has(n,"CALF"))
    return {HitZones::Limb,6};
  if(has(n,"TOE"))
    return {HitZones::Limb,4};
  if(has(n,"FOOT"))
    return {HitZones::Limb,5};
  if(has(n,"TAIL"))
    return {HitZones::Limb,5};
  if(has(n,"LEG") || has(n,"ARM") || has(n,"CLAW") || has(n,"ZANGE") || has(n,"PINCER") || has(n,"FUEHLER") || has(n,"WING"))
    return {HitZones::Limb,4};
  if(has(n,"PELVIS"))
    return {HitZones::Body,14};
  if(has(n,"SPINE"))
    return {HitZones::Body,15};
  return {HitZones::Body,12};
  }

// a ray (rd: unit) and a sphere: the distance it enters at, 0 from inside, -1: misses
float sphereHit(const Vec3& ro, const Vec3& rd, const Vec3& c, float r) {
  const Vec3  oc = ro - c;
  const float b  = dot(oc,rd);
  const float cc = dot(oc,oc) - r*r;
  if(cc<=0.f)
    return 0.f;
  const float h = b*b - cc;
  if(h<0.f)
    return -1.f;
  const float t = -b - std::sqrt(h);
  return t>=0.f ? t : -1.f;
  }

// a ray and a capsule from pa to pb
float capsuleHit(const Vec3& ro, const Vec3& rd, const Vec3& pa, const Vec3& pb, float r) {
  const Vec3  ba   = pb - pa;
  const float baba = dot(ba,ba);
  float       best = -1.f;
  auto take = [&](float t) {
    if(t>=0.f && (best<0.f || t<best))
      best = t;
    };
  if(baba>1e-4f) {
    const Vec3  oa   = ro - pa;
    const float k    = std::clamp(dot(oa,ba)/baba,0.f,1.f);
    if(length(oa - ba*k)<=r)
      return 0.f;
    const float bard = dot(ba,rd);
    const float baoa = dot(ba,oa);
    const float a    = baba - bard*bard;
    if(a>1e-6f) {
      const float b = baba*dot(rd,oa) - baoa*bard;
      const float c = baba*dot(oa,oa) - baoa*baoa - r*r*baba;
      const float h = b*b - a*c;
      if(h<0.f)
        return -1.f; // misses the endless cylinder: the caps lie inside it
      const float t = (-b - std::sqrt(h))/a;
      const float y = baoa + t*bard;
      if(y>0.f && y<baba)
        take(t);
      }
    take(sphereHit(ro,rd,pb,r));
    }
  take(sphereHit(ro,rd,pa,r));
  return best;
  }

}

void HitZones::setSkeleton(const Skeleton* sk) {
  caps.clear();
  if(sk==nullptr || sk->tr.size()!=sk->nodes.size())
    return;

  auto& nodes = sk->nodes;
  auto  skip  = [&](size_t i) {
    return nodes[i].parent==size_t(-1) || isSlot(nodes[i].name) || has(nodes[i].name,"FOOTSTEPS");
    };
  auto  pos   = [&](size_t i) {
    return position(sk->tr[i]);
    };
  // a point (in the model) in the frame of a bone
  auto  local = [&](size_t bone, const Vec3& p) {
    Matrix4x4 inv = sk->tr[bone];
    inv.inverse();
    Vec3 at = p;
    inv.project(at);
    return End{bone,at};
    };

  // how big the creature is: the radii are for a human
  Vec3 lo = {}, hi = {};
  bool any = false;
  for(size_t i=0; i<nodes.size(); ++i) {
    if(skip(i))
      continue;
    const Vec3 p = pos(i);
    lo  = any ? Vec3(std::min(lo.x,p.x),std::min(lo.y,p.y),std::min(lo.z,p.z)) : p;
    hi  = any ? Vec3(std::max(hi.x,p.x),std::max(hi.y,p.y),std::max(hi.z,p.z)) : p;
    any = true;
    }
  if(!any)
    return;
  const float k = std::max({hi.x-lo.x,hi.y-lo.y,hi.z-lo.z})/RefSize;
  if(k<=0.01f)
    return;

  for(size_t i=0; i<nodes.size(); ++i) {
    if(skip(i))
      continue;
    const Kind ki = kindOf(nodes[i].name);
    const Vec3 pi = pos(i);

    // from the parent to the bone: the parent's zone and thickness
    const size_t p = nodes[i].parent;
    if(p<nodes.size() && !skip(p)) {
      const Kind kp = kindOf(nodes[p].name);
      const Vec3 pp = pos(p);
      Capsule c;
      c.a    = End{p,Vec3()};
      c.b    = End{i,Vec3()};
      c.r    = kp.r*k;
      c.zone = kp.zone;
      // the trunk ends where the neck, the head begin: else its round end covers the chin
      const float len = length(pi-pp);
      if(kp.zone==Body && (ki.zone==Head || has(nodes[i].name,"NECK")) && len>0.f)
        c.b = local(i, pi - (pi-pp)*(std::min(c.r,len)/len));
      caps.push_back(c);
      }

    bool children = false;
    End  helmet   = {size_t(-1),Vec3()};
    for(size_t j=0; j<nodes.size(); ++j) {
      if(nodes[j].parent!=i)
        continue;
      if(!skip(j))
        children = true;
      else if(nodes[j].name=="ZS_HELMET")
        helmet = End{j,Vec3()};
      }

    if(ki.zone==Head && has(nodes[i].name,"HEAD")) {
      // the skull: up to the helmet slot (a human), else on past the bone, away from the neck
      Capsule c;
      c.a    = End{i,Vec3()};
      c.r    = ki.r*k;
      c.zone = Head;
      if(helmet.bone!=size_t(-1)) {
        c.b = helmet;
        } else {
        Vec3 dir = p<nodes.size() ? normalize(pi-pos(p)) : Vec3(0,1,0);
        if(length(dir)<0.5f)
          dir = Vec3(0,1,0);
        c.b = local(i, pi + dir*(c.r*1.2f));
        }
      caps.push_back(c);
      }
    else if(!children) {
      // an end: a hand, a toe, a tail tip
      Capsule c;
      c.a    = End{i,Vec3()};
      c.b    = c.a;
      c.r    = ki.r*k;
      c.zone = ki.zone;
      caps.push_back(c);
      }
    }
  }

Vec3 HitZones::point(const Matrix4x4* tr, const End& e) {
  Vec3 p = e.at;
  tr[e.bone].project(p);
  return p;
  }

HitZones::Zone HitZones::rayTest(const Matrix4x4* tr, size_t numBones, float scale, const Vec3& from, const Vec3& to, float& t) const {
  t = 0;
  const Vec3  d   = to - from;
  const float len = length(d);
  if(tr==nullptr || len<1e-3f)
    return None;
  const Vec3 rd   = d*(1.f/len);
  float      best = len;
  Zone       ret  = None;
  for(auto& c:caps) {
    if(c.a.bone>=numBones || c.b.bone>=numBones)
      continue;
    const float h = capsuleHit(from,rd,point(tr,c.a),point(tr,c.b),c.r*scale);
    if(h>=0.f && h<best) {
      best = h;
      ret  = c.zone;
      }
    }
  t = best;
  return ret;
  }

bool HitZones::center(const Matrix4x4* tr, size_t numBones, Zone z, Vec3& at) const {
  // the head, the trunk: the middle of their capsules; a limb: the biggest one (a thigh)
  Vec3  sum  = {};
  float wsum = 0, wmax = 0;
  for(auto& c:caps) {
    if(c.zone!=z || c.a.bone>=numBones || c.b.bone>=numBones)
      continue;
    const Vec3  pa = point(tr,c.a), pb = point(tr,c.b);
    const float w  = (length(pb-pa) + 2.f*c.r)*c.r*c.r;
    if(z==Limb) {
      if(w>wmax) {
        wmax = w;
        at   = (pa+pb)*0.5f;
        }
      continue;
      }
    sum  += (pa+pb)*(0.5f*w);
    wsum += w;
    }
  if(z==Limb)
    return wmax>0.f;
  if(wsum<=0.f)
    return false;
  at = sum*(1.f/wsum);
  return true;
  }

void HitZones::posed(const Matrix4x4* tr, size_t numBones, float scale, std::vector<Posed>& out) const {
  out.clear();
  for(auto& c:caps) {
    if(c.a.bone>=numBones || c.b.bone>=numBones)
      continue;
    out.push_back({point(tr,c.a),point(tr,c.b),c.r*scale,c.zone});
    }
  }

std::string HitZones::dump(const Matrix4x4* tr, size_t numBones, float scale) const {
  std::string out;
  for(auto& c:caps) {
    if(c.a.bone>=numBones || c.b.bone>=numBones)
      continue;
    const Vec3 a = point(tr,c.a), b = point(tr,c.b);
    char buf[160];
    std::snprintf(buf,sizeof(buf),"  %s %zu-%zu: %.0f %.0f %.0f - %.0f %.0f %.0f r %.1f\n",std::string(name(c.zone)).c_str(),c.a.bone,c.b.bone,
                  a.x,a.y,a.z,b.x,b.y,b.z,double(c.r*scale));
    out += buf;
    }
  return out;
  }

std::string_view HitZones::name(Zone z) {
  switch(z) {
    case None: return "none";
    case Body: return "body";
    case Head: return "head";
    case Limb: return "limb";
    }
  return "?";
  }
