#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <algorithm>
#include <cmath>
#include <vector>

// Small math for the post-passes over world-space bone matrices (FootIk, Gaze, BowDraw)
namespace IkMath {

using Tempest::Vec3;
using Tempest::Matrix4x4;

constexpr float Eps = 1e-4f;
inline const Vec3 Up = {0,1,0};

// row-major: m[row][col]
struct Mat3 final {
  float m[3][3];
  };

inline float dot(const Vec3& a, const Vec3& b) {
  return a.x*b.x + a.y*b.y + a.z*b.z;
  }

inline Vec3 cross(const Vec3& a, const Vec3& b) {
  return {a.y*b.z - a.z*b.y, a.z*b.x - a.x*b.z, a.x*b.y - a.y*b.x};
  }

inline float length(const Vec3& v) {
  return std::sqrt(dot(v,v));
  }

inline Vec3 normalize(const Vec3& v) {
  const float l = length(v);
  return l>Eps ? v*(1.f/l) : Vec3();
  }

inline Vec3 position(const Matrix4x4& m) {
  return {m.at(3,0), m.at(3,1), m.at(3,2)};
  }

inline Mat3 mul(const Mat3& a, const Mat3& b) {
  Mat3 r = {};
  for(int i=0; i<3; ++i)
    for(int j=0; j<3; ++j)
      r.m[i][j] = a.m[i][0]*b.m[0][j] + a.m[i][1]*b.m[1][j] + a.m[i][2]*b.m[2][j];
  return r;
  }

inline Vec3 mul(const Mat3& a, const Vec3& v) {
  return {a.m[0][0]*v.x + a.m[0][1]*v.y + a.m[0][2]*v.z,
          a.m[1][0]*v.x + a.m[1][1]*v.y + a.m[1][2]*v.z,
          a.m[2][0]*v.x + a.m[2][1]*v.y + a.m[2][2]*v.z};
  }

inline Mat3 transpose(const Mat3& a) {
  Mat3 r = {};
  for(int i=0; i<3; ++i)
    for(int j=0; j<3; ++j)
      r.m[i][j] = a.m[j][i];
  return r;
  }

// Shortest-arc rotation that takes unit vector u to unit vector v
inline Mat3 rotationBetween(const Vec3& u, const Vec3& v) {
  const Vec3  k = cross(u,v);
  const float c = dot(u,v);
  if(c<-1.f+Eps) {
    // opposite vectors: half turn around any perpendicular axis
    const Vec3 a = normalize(cross(u, std::abs(u.x)<0.9f ? Vec3(1,0,0) : Vec3(0,1,0)));
    const float x[3] = {a.x, a.y, a.z};
    Mat3 r = {};
    for(int i=0; i<3; ++i)
      for(int j=0; j<3; ++j)
        r.m[i][j] = 2.f*x[i]*x[j] - (i==j ? 1.f : 0.f);
    return r;
    }
  // R = c*I + [k]x + k*k^T/(1+c)
  const float s = 1.f/(1.f+c);
  return Mat3{{
    {c + k.x*k.x*s,   k.x*k.y*s - k.z, k.x*k.z*s + k.y},
    {k.x*k.y*s + k.z, c + k.y*k.y*s,   k.y*k.z*s - k.x},
    {k.x*k.z*s - k.y, k.y*k.z*s + k.x, c + k.z*k.z*s  },
    }};
  }

// Rotation by angle a around unit axis u
inline Mat3 axisAngle(const Vec3& u, float a) {
  const float c = std::cos(a), s = std::sin(a), t = 1.f-c;
  return Mat3{{
    {t*u.x*u.x + c,     t*u.x*u.y - s*u.z, t*u.x*u.z + s*u.y},
    {t*u.x*u.y + s*u.z, t*u.y*u.y + c,     t*u.y*u.z - s*u.x},
    {t*u.x*u.z - s*u.y, t*u.y*u.z + s*u.x, t*u.z*u.z + c    },
    }};
  }

inline Mat3 fromColumns(const Vec3& x, const Vec3& y, const Vec3& z) {
  return Mat3{{
    {x.x, y.x, z.x},
    {x.y, y.y, z.y},
    {x.z, y.z, z.z},
    }};
  }

// Rotation R around pivot p as a matrix for world-space bones: tr = W*tr, W = [R | p - R*p]
inline Matrix4x4 rotationMatrix(const Mat3& r, const Vec3& p) {
  const Vec3 rp = mul(r,p);
  Matrix4x4 w;
  for(int row=0; row<3; ++row)
    for(int col=0; col<3; ++col)
      w.set(col,row,r.m[row][col]);
  w.set(0,3,0);
  w.set(1,3,0);
  w.set(2,3,0);
  w.set(3,0,p.x-rp.x);
  w.set(3,1,p.y-rp.y);
  w.set(3,2,p.z-rp.z);
  w.set(3,3,1);
  return w;
  }

inline void rotateAbout(Matrix4x4* tr, const std::vector<size_t>& bones, const Mat3& r, const Vec3& p) {
  const Matrix4x4 w = rotationMatrix(r,p);
  for(auto i:bones)
    tr[i] = w*tr[i];
  }

inline void rotateAll(Matrix4x4* tr, size_t numBones, const Mat3& r, const Vec3& p) {
  const Matrix4x4 w = rotationMatrix(r,p);
  for(size_t i=0; i<numBones; ++i)
    tr[i] = w*tr[i];
  }

// The turn of a bone's matrix (the columns normalized: a scale is dropped)
inline Mat3 rotationOf(const Matrix4x4& m) {
  Mat3 r = {};
  for(int col=0; col<3; ++col) {
    const Vec3 c = normalize(Vec3(m.at(col,0),m.at(col,1),m.at(col,2)));
    r.m[0][col] = c.x;
    r.m[1][col] = c.y;
    r.m[2][col] = c.z;
    }
  return r;
  }

// A bone's matrix of a turn and a place
inline Matrix4x4 compose(const Mat3& r, const Vec3& p) {
  Matrix4x4 m;
  m.identity();
  for(int row=0; row<3; ++row)
    for(int col=0; col<3; ++col)
      m.set(col,row,r.m[row][col]);
  m.set(3,0,p.x);
  m.set(3,1,p.y);
  m.set(3,2,p.z);
  return m;
  }

struct Quat final {
  float w = 1, x = 0, y = 0, z = 0;
  };

inline Quat toQuat(const Mat3& a) {
  const auto& m = a.m;
  Quat        q;
  const float t = m[0][0]+m[1][1]+m[2][2];
  if(t>0.f) {
    const float s = std::sqrt(t+1.f)*2.f;
    q = {0.25f*s, (m[2][1]-m[1][2])/s, (m[0][2]-m[2][0])/s, (m[1][0]-m[0][1])/s};
    }
  else if(m[0][0]>m[1][1] && m[0][0]>m[2][2]) {
    const float s = std::sqrt(1.f+m[0][0]-m[1][1]-m[2][2])*2.f;
    q = {(m[2][1]-m[1][2])/s, 0.25f*s, (m[0][1]+m[1][0])/s, (m[0][2]+m[2][0])/s};
    }
  else if(m[1][1]>m[2][2]) {
    const float s = std::sqrt(1.f+m[1][1]-m[0][0]-m[2][2])*2.f;
    q = {(m[0][2]-m[2][0])/s, (m[0][1]+m[1][0])/s, 0.25f*s, (m[1][2]+m[2][1])/s};
    }
  else {
    const float s = std::sqrt(1.f+m[2][2]-m[0][0]-m[1][1])*2.f;
    q = {(m[1][0]-m[0][1])/s, (m[0][2]+m[2][0])/s, (m[1][2]+m[2][1])/s, 0.25f*s};
    }
  return q;
  }

inline Mat3 toMat3(const Quat& q) {
  const float w = q.w, x = q.x, y = q.y, z = q.z;
  return Mat3{{
    {1-2*(y*y+z*z), 2*(x*y-w*z),   2*(x*z+w*y)  },
    {2*(x*y+w*z),   1-2*(x*x+z*z), 2*(y*z-w*x)  },
    {2*(x*z-w*y),   2*(y*z+w*x),   1-2*(x*x+y*y)},
    }};
  }

// The turn between a and b, t of the way (the shorter way round)
inline Quat slerp(const Quat& a, Quat b, float t) {
  float c = a.w*b.w + a.x*b.x + a.y*b.y + a.z*b.z;
  if(c<0.f) {
    b = {-b.w,-b.x,-b.y,-b.z};
    c = -c;
    }
  float ka = 1.f-t, kb = t;
  if(c<0.9995f) {
    const float an = std::acos(c);
    const float s  = std::sin(an);
    ka = std::sin((1.f-t)*an)/s;
    kb = std::sin(t*an)/s;
    }
  Quat q = {ka*a.w+kb*b.w, ka*a.x+kb*b.x, ka*a.y+kb*b.y, ka*a.z+kb*b.z};
  const float l = std::sqrt(q.w*q.w+q.x*q.x+q.y*q.y+q.z*q.z);
  if(l>0.f) {
    q.w /= l; q.x /= l; q.y /= l; q.z /= l;
    }
  return q;
  }

// Two bone matrices blended: the turn t of the way round, the place t of the way
inline Matrix4x4 blend(const Matrix4x4& a, const Matrix4x4& b, float t) {
  if(t<=0.f)
    return a;
  if(t>=1.f)
    return b;
  const Quat q = slerp(toQuat(rotationOf(a)),toQuat(rotationOf(b)),t);
  return compose(toMat3(q),position(a)+(position(b)-position(a))*t);
  }

// How twoBone turned the bones: the first (r0, about its root), then the second (r1, about the middle
// joint); where the end went
struct TwoBone final {
  Mat3 r0  = {};
  Mat3 r1  = {};
  Vec3 end = {};
  };

// Two bones root-mid-end (hip, knee, ankle; shoulder, elbow, wrist) bent so the end goes to target. The
// middle joint bends in the plane of the bones and `bend` (where it leans), turned toward the target; the
// reach is kept short of straight (maxReach of the bones' length, or as straight as it is already) and,
// with minFold, from folding up tighter than that. rootTree, midTree: the bones that turn with the first
// and the second bone. False: degenerate, nothing changed
inline bool twoBone(Matrix4x4* tr, size_t root, size_t mid, size_t end,
                    const std::vector<size_t>& rootTree, const std::vector<size_t>& midTree,
                    const Vec3& target, const Vec3& bend, float maxReach, float minFold, TwoBone& out) {
  const Vec3  a   = position(tr[root]);
  const Vec3  b   = position(tr[mid]);
  const Vec3  f   = position(tr[end]);
  const float lab = length(b-a);
  const float lbf = length(f-b);
  const Vec3  at  = target-a;
  float       lat = length(at);
  if(lab<Eps || lbf<Eps || lat<Eps)
    return false;

  const Vec3  dir  = at*(1.f/lat);
  const float lmax = std::min(std::max((lab+lbf)*maxReach, length(f-a)), (lab+lbf)*0.999f);
  float       lmin = std::abs(lab-lbf)+Eps;
  if(minFold>0.f)
    lmin = std::max(lmin, std::min((lab+lbf)*minFold, length(f-a)));
  lat = std::min(std::max(lat,lmin),lmax);

  const Vec3 pole = normalize(cross(cross(f-a,bend),dir));
  if(length(pole)<Eps)
    return false;

  const float cosA = std::min(std::max((lab*lab + lat*lat - lbf*lbf)/(2.f*lab*lat), -1.f), 1.f);
  const float sinA = std::sqrt(1.f - cosA*cosA);
  const Vec3  knee = a + dir*(lab*cosA) + pole*(lab*sinA);
  out.end          = a + dir*lat;

  out.r0 = rotationBetween(normalize(b-a), normalize(knee-a));
  rotateAbout(tr,rootTree,out.r0,a);

  const Vec3 b1 = position(tr[mid]);
  const Vec3 f1 = position(tr[end]);
  out.r1 = rotationBetween(normalize(f1-b1), normalize(out.end-b1));
  rotateAbout(tr,midTree,out.r1,b1);
  return true;
  }

}
