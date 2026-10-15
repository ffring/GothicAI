#pragma once

#include <Tempest/Matrix4x4>
#include <cmath>

namespace PhysicsSave {

inline bool sameTransform(const Tempest::Matrix4x4& a, const Tempest::Matrix4x4& b, float tolerance = .001f) {
  for(size_t i=0;i<16;++i)
    if(!std::isfinite(a.data()[i]) || !std::isfinite(b.data()[i]) ||
       std::abs(a.data()[i]-b.data()[i])>tolerance)
      return false;
  return true;
  }

inline bool validTransform(const Tempest::Matrix4x4& m) {
  for(size_t i=0;i<16;++i)
    if(!std::isfinite(m.data()[i]) || std::abs(m.data()[i])>100000000.f)
      return false;
  if(std::abs(m.at(0,3))>.00001f || std::abs(m.at(1,3))>.00001f ||
     std::abs(m.at(2,3))>.00001f || std::abs(m.at(3,3)-1.f)>.00001f)
    return false;
  double axes[3][3], lengths[3] = {};
  for(int a=0;a<3;++a) {
    for(int c=0;c<3;++c)
      lengths[a] += double(m.at(a,c))*m.at(a,c);
    if(lengths[a]<1.e-8 || lengths[a]>10000.)
      return false;
    for(int c=0;c<3;++c)
      axes[a][c] = m.at(a,c)/std::sqrt(lengths[a]);
    }
  for(int a=0;a<3;++a)
    for(int b=a+1;b<3;++b) {
      double dot=0;
      for(int c=0;c<3;++c)
        dot += axes[a][c]*axes[b][c];
      if(std::abs(dot)>.001)
        return false;
      }
  const auto& x=axes[0]; const auto& y=axes[1]; const auto& z=axes[2];
  return (x[1]*y[2]-x[2]*y[1])*z[0]+(x[2]*y[0]-x[0]*y[2])*z[1]+(x[0]*y[1]-x[1]*y[0])*z[2]>.999;
  }

inline bool sameScale(const Tempest::Matrix4x4& a, const Tempest::Matrix4x4& b) {
  for(int axis=0;axis<3;++axis) {
    float la=0,lb=0;
    for(int c=0;c<3;++c) {
      la+=a.at(axis,c)*a.at(axis,c);
      lb+=b.at(axis,c)*b.at(axis,c);
      }
    if(std::abs(la-lb)>.001f*lb)
      return false;
    }
  return true;
  }

}
