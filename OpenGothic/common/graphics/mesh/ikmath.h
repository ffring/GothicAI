#pragma once

#include <Tempest/Matrix4x4>
#include <Tempest/Vec>

#include <cmath>
#include <vector>

// Small math for the post-passes over world-space bone matrices (FootIk, Gaze)
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

}
