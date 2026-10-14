#include "ragdollsave.h"

#include "resources.h"
#include "serialize.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <type_traits>
#include <vector>

namespace {
  constexpr uint32_t magic = 0x32444752u; // RGD2, using Serialize's native byte order.
  constexpr uint16_t schema = 1;
  constexpr uint8_t legacyFlag = 1u << 0;
  constexpr uint8_t bodyFlag = 1u << 1;
  constexpr uint8_t knownFlags = legacyFlag | bodyFlag;
  constexpr uint32_t maxSkeletonBytes = 512;
  constexpr float maxCoordinate = 100000000.f; // centimetres, a 1000 km world.
  constexpr float maxLinearSpeed = 1000000.f; // centimetres/second.
  constexpr float maxAngularSpeed = 10000.f;  // radians/second.
  constexpr size_t partBytesV1 = sizeof(uint32_t) + 16*sizeof(float) + 6*sizeof(float) + sizeof(uint8_t);
  constexpr size_t partBytesV2 = partBytesV1 + 16*sizeof(float);
  constexpr size_t partBytesV3 = partBytesV2 + 10*sizeof(float);
  constexpr size_t ancestorBytes = 2*sizeof(uint32_t) + 16*sizeof(float);
  constexpr size_t maxPayloadBytes = 2*sizeof(uint32_t) + maxSkeletonBytes +
                                     16*sizeof(float) + sizeof(uint32_t) +
                                     Resources::MAX_NUM_SKELETAL_NODES*partBytesV3 + sizeof(uint8_t) + sizeof(uint32_t) +
                                     Resources::MAX_NUM_SKELETAL_NODES*ancestorBytes;

  bool supportedVersion(uint32_t version) {
    return version>=1 && version<=3;
    }

  size_t partBytes(uint32_t version) {
    return version==1 ? partBytesV1 : version==2 ? partBytesV2 : partBytesV3;
    }

  bool finiteVector(const Tempest::Vec3& vector, float bound) {
    return std::isfinite(vector.x) && std::abs(vector.x)<=bound &&
           std::isfinite(vector.y) && std::abs(vector.y)<=bound &&
           std::isfinite(vector.z) && std::abs(vector.z)<=bound;
    }

  bool validAffine(const Tempest::Matrix4x4& matrix) {
    for(int row=0; row<4; ++row)
      for(int column=0; column<4; ++column)
        if(!std::isfinite(matrix.at(row,column)))
          return false;
    if(std::abs(matrix.at(0,3))>0.00001f || std::abs(matrix.at(1,3))>0.00001f ||
       std::abs(matrix.at(2,3))>0.00001f || std::abs(matrix.at(3,3)-1.f)>0.00001f)
      return false;
    if(!finiteVector({matrix.at(3,0),matrix.at(3,1),matrix.at(3,2)},maxCoordinate))
      return false;

    return true;
    }

  bool validMatrix(const Tempest::Matrix4x4& matrix, std::array<double,3>& scale) {
    if(!validAffine(matrix))
      return false;

    // Preserve scale exactly, but reject singular, skewed or reflected bases.
    // A quaternion-based body cannot represent their orientation reliably.
    std::array<std::array<double,3>,3> basis = {};
    for(size_t axis=0; axis<3; ++axis) {
      double lengthSquared = 0;
      for(size_t component=0; component<3; ++component) {
        basis[axis][component] = matrix.at(int(axis),int(component));
        lengthSquared += basis[axis][component]*basis[axis][component];
        }
      const double length = std::sqrt(lengthSquared);
      if(length<double(0.0001f) || length>100.)
        return false;
      scale[axis] = length;
      for(double& component:basis[axis])
        component /= length;
      }
    for(size_t axis=0; axis<3; ++axis)
      for(size_t other=axis+1; other<3; ++other) {
        double dot = 0;
        for(size_t component=0; component<3; ++component)
          dot += basis[axis][component]*basis[other][component];
        if(std::abs(dot)>0.001)
          return false;
        }
    const auto& x = basis[0];
    const auto& y = basis[1];
    const auto& z = basis[2];
    const double handedness = (x[1]*y[2]-x[2]*y[1])*z[0] +
                              (x[2]*y[0]-x[0]*y[2])*z[1] +
                              (x[0]*y[1]-x[1]*y[0])*z[2];
    return handedness>0.999;
    }

  bool uniformScale(const std::array<double,3>& scale, double expected) {
    return std::abs(scale[0]-expected)<=expected*0.001 &&
           std::abs(scale[1]-expected)<=expected*0.001 &&
           std::abs(scale[2]-expected)<=expected*0.001;
    }

  bool validReference(const Tempest::Matrix4x4& matrix, std::array<double,3> objectScale) {
    if(!validAffine(matrix))
      return false;
    std::array<std::array<double,3>,3> basis = {};
    for(size_t axis=0; axis<3; ++axis)
      for(size_t component=0; component<3; ++component)
        basis[axis][component] = matrix.at(int(axis),int(component));
    const auto& x = basis[0];
    const auto& y = basis[1];
    const auto& z = basis[2];
    const double determinant = (x[1]*y[2]-x[2]*y[1])*z[0] +
                               (x[2]*y[0]-x[0]*y[2])*z[1] +
                               (x[0]*y[1]-x[1]*y[0])*z[2];
    if(!std::isfinite(determinant) || determinant<=0)
      return false;

    // An animated bone under an anisotropic object transform may be sheared.
    // Its singular values must still be the object's scale, independently of
    // the bone rotation. Jacobi diagonalization of B*B^T avoids assuming that
    // those sheared row lengths are the scale. The backend repeats this check
    // against the actual skeleton/creation geometry before applying the state.
    std::array<std::array<double,3>,3> gram = {};
    for(size_t row=0; row<3; ++row)
      for(size_t column=0; column<3; ++column)
        for(size_t component=0; component<3; ++component)
          gram[row][column] += basis[row][component]*basis[column][component];
    for(size_t iteration=0; iteration<32; ++iteration) {
      size_t p = 0, q = 1;
      for(size_t row=0; row<3; ++row)
        for(size_t column=row+1; column<3; ++column)
          if(std::abs(gram[row][column])>std::abs(gram[p][q])) {
            p = row;
            q = column;
            }
      if(std::abs(gram[p][q])<=1.e-20*std::max({gram[0][0],gram[1][1],gram[2][2],1.e-8}))
        break;
      const double tau = (gram[q][q]-gram[p][p])/(2*gram[p][q]);
      const double t = std::copysign(1/(std::abs(tau)+std::hypot(1.,tau)),tau);
      const double c = 1/std::sqrt(1+t*t), s = t*c;
      const double off = gram[p][q];
      gram[p][p] -= t*off;
      gram[q][q] += t*off;
      gram[p][q] = gram[q][p] = 0;
      for(size_t axis=0; axis<3; ++axis) {
        if(axis==p || axis==q)
          continue;
        const double ap = gram[axis][p], aq = gram[axis][q];
        gram[axis][p] = gram[p][axis] = c*ap-s*aq;
        gram[axis][q] = gram[q][axis] = s*ap+c*aq;
        }
      }
    std::array<double,3> scale = {};
    for(size_t axis=0; axis<3; ++axis) {
      if(!std::isfinite(gram[axis][axis]) || gram[axis][axis]<=0)
        return false;
      scale[axis] = std::sqrt(gram[axis][axis]);
      }
    std::sort(scale.begin(),scale.end());
    std::sort(objectScale.begin(),objectScale.end());
    for(size_t axis=0; axis<3; ++axis)
      if(std::abs(scale[axis]-objectScale[axis])>objectScale[axis]*0.001)
        return false;
    return true;
    }

  bool validNativeState(const Ragdoll::PartState& part) {
    if(!finiteVector(part.centerOfMassMeters,maxCoordinate*0.01f) ||
       !finiteVector(part.linearVelocityMeters,maxLinearSpeed*0.01f))
      return false;
    const auto& q = part.rotation;
    if(!std::isfinite(q.x) || !std::isfinite(q.y) || !std::isfinite(q.z) || !std::isfinite(q.w))
      return false;
    const double x=q.x, y=q.y, z=q.z, w=q.w;
    if(std::abs(x*x+y*y+z*z+w*w-1.)>0.001)
      return false;
    const double basis[3][3] = {
      {1-2*(y*y+z*z),2*(x*y+z*w),2*(x*z-y*w)},
      {2*(x*y-z*w),1-2*(x*x+z*z),2*(y*z+x*w)},
      {2*(x*z+y*w),2*(y*z-x*w),1-2*(x*x+y*y)}
      };
    for(int axis=0; axis<3; ++axis)
      for(int component=0; component<3; ++component)
        if(std::abs(basis[axis][component]-part.transform.at(axis,component))>0.001)
          return false;
    // Compare with the actual binary32 export operation, not a decimal or
    // double cm/m round trip. Native bits are preserved, never reconstructed.
    const float native[] = {part.linearVelocityMeters.x,part.linearVelocityMeters.y,part.linearVelocityMeters.z};
    const float publicCm[] = {part.linearVelocity.x,part.linearVelocity.y,part.linearVelocity.z};
    for(size_t axis=0; axis<3; ++axis) {
      const float exported = native[axis]*100.f;
      if(std::memcmp(&exported,&publicCm[axis],sizeof(float))!=0)
        return false;
      }
    // COM-to-origin agreement depends on the rebuilt shape's local COM. The
    // backend verifies it against the public cm transform before any mutation.
    return true;
    }

  bool validState(const Ragdoll::State& body) {
    std::array<double,3> objectScale = {};
    if(!supportedVersion(body.version) || body.skeleton.empty() || body.skeleton.size()>maxSkeletonBytes ||
       body.skeleton.find('\0')!=std::string::npos || !validMatrix(body.rootTransform,objectScale) ||
       body.parts.empty() || body.parts.size()>Resources::MAX_NUM_SKELETAL_NODES)
      return false;
    if(body.version==1 && !uniformScale(objectScale,objectScale[0]))
      return false;
    if(body.ancestors.size()>Resources::MAX_NUM_SKELETAL_NODES ||
       (body.version<3 && (!body.ancestors.empty() || body.legacyAncestors)) ||
       (body.legacyAncestors && !body.ancestors.empty()))
      return false;
    std::array<bool,Resources::MAX_NUM_SKELETAL_NODES> seen = {};
    for(const auto& part:body.parts) {
      std::array<double,3> partScale = {};
      if(part.bone>=seen.size() || seen[part.bone] || !validMatrix(part.transform,partScale) ||
         !finiteVector(part.linearVelocity,maxLinearSpeed) ||
         !finiteVector(part.angularVelocity,maxAngularSpeed))
        return false;
      if(body.version==1 ? !uniformScale(partScale,objectScale[0]) :
                          (!uniformScale(partScale,1.) || !validReference(part.referenceTransform,objectScale)))
        return false;
      if(body.version>=3 && !validNativeState(part))
        return false;
      seen[part.bone] = true;
      }
    const auto physical = seen;
    for(const auto& ancestor:body.ancestors) {
      if(ancestor.bone>=seen.size() || ancestor.anchorBone>=physical.size() || seen[ancestor.bone] ||
         !physical[ancestor.anchorBone] || !validReference(ancestor.referenceTransform,objectScale))
        return false;
      seen[ancestor.bone] = true;
      }
    return true;
    }

  class Payload final {
    public:
      template<class T>
      void write(const T& value) {
        static_assert(std::is_trivially_copyable_v<T>);
        const size_t at = bytes.size();
        bytes.resize(at+sizeof(value));
        std::memcpy(bytes.data()+at,&value,sizeof(value));
        }

      void writeMatrix(const Tempest::Matrix4x4& matrix) {
        for(int row=0; row<4; ++row)
          for(int column=0; column<4; ++column)
            write(matrix.at(row,column));
        }

      void writeVector(const Tempest::Vec3& vector) {
        write(vector.x);
        write(vector.y);
        write(vector.z);
        }

      void writeQuaternion(const Tempest::Vec4& vector) {
        write(vector.x);
        write(vector.y);
        write(vector.z);
        write(vector.w);
        }

      template<class T>
      bool read(T& value) {
        static_assert(std::is_trivially_copyable_v<T>);
        if(offset>bytes.size() || sizeof(value)>bytes.size()-offset)
          return false;
        std::memcpy(&value,bytes.data()+offset,sizeof(value));
        offset += sizeof(value);
        return true;
        }

      bool readMatrix(Tempest::Matrix4x4& matrix) {
        for(int row=0; row<4; ++row)
          for(int column=0; column<4; ++column) {
            float value = 0;
            if(!read(value))
              return false;
            matrix.set(row,column,value);
            }
        return true;
        }

      bool readVector(Tempest::Vec3& vector) {
        return read(vector.x) && read(vector.y) && read(vector.z);
        }

      bool readQuaternion(Tempest::Vec4& vector) {
        return read(vector.x) && read(vector.y) && read(vector.z) && read(vector.w);
        }

      bool readString(std::string& value) {
        uint32_t length = 0;
        if(!read(length) || length==0 || length>maxSkeletonBytes ||
           offset>bytes.size() || length>bytes.size()-offset)
          return false;
        value.assign(reinterpret_cast<const char*>(bytes.data()+offset),length);
        offset += length;
        return true;
        }

      std::vector<uint8_t> bytes;
      size_t offset = 0;
    };

  bool entryExhausted(Serialize& fin) {
    uint8_t extra = 0;
    try {
      fin.read(extra);
      return false;
      }
    catch(const std::runtime_error&) {
      // Serialize::readBytes rejects an attempted read beyond this ZIP entry.
      return true;
      }
    }
  }

bool RagdollSave::valid(const Snapshot& snapshot) {
  return !snapshot.body || validState(*snapshot.body);
  }

void RagdollSave::save(Serialize& fout, const Snapshot& snapshot) {
  if(!valid(snapshot))
    throw std::invalid_argument("invalid ragdoll save snapshot");

  Payload payload;
  if(snapshot.body) {
    const auto& body = *snapshot.body;
    payload.write(body.version);
    payload.write(uint32_t(body.skeleton.size()));
    payload.bytes.insert(payload.bytes.end(),body.skeleton.begin(),body.skeleton.end());
    payload.writeMatrix(body.rootTransform);
    payload.write(uint32_t(body.parts.size()));
    for(const auto& part:body.parts) {
      payload.write(part.bone);
      payload.writeMatrix(part.transform);
      if(body.version>=2)
        payload.writeMatrix(part.referenceTransform);
      if(body.version>=3) {
        payload.writeVector(part.centerOfMassMeters);
        payload.writeQuaternion(part.rotation);
        payload.writeVector(part.linearVelocityMeters);
        }
      payload.writeVector(part.linearVelocity);
      payload.writeVector(part.angularVelocity);
      payload.write(uint8_t(part.sleeping ? 1 : 0));
      }
    if(body.version>=3) {
      payload.write(uint8_t(body.legacyAncestors ? 1 : 0));
      payload.write(uint32_t(body.ancestors.size()));
      for(const auto& ancestor:body.ancestors) {
        payload.write(ancestor.bone);
        payload.write(ancestor.anchorBone);
        payload.writeMatrix(ancestor.referenceTransform);
        }
      }
    }
  const uint8_t flags = uint8_t((snapshot.legacyDown ? legacyFlag : 0) |
                                (snapshot.body ? bodyFlag : 0));
  fout.write(magic,schema,flags,uint8_t(0),uint32_t(payload.bytes.size()));
  fout.writeBytes(payload.bytes.data(),payload.bytes.size());
  }

bool RagdollSave::load(Serialize& fin, Snapshot& snapshot) {
  try {
    uint32_t fileMagic = 0, size = 0;
    uint16_t fileSchema = 0;
    uint8_t flags = 0, reserved = 0;
    fin.read(fileMagic,fileSchema,flags,reserved,size);
    if(fileMagic!=magic || fileSchema!=schema || (flags & ~knownFlags)!=0 || reserved!=0 ||
       size>maxPayloadBytes || (!(flags & bodyFlag) && size!=0) ||
       ((flags & bodyFlag) && size==0))
      return false;

    Snapshot result;
    result.legacyDown = bool(flags & legacyFlag);
    if(flags & bodyFlag) {
      Payload payload;
      payload.bytes.resize(size);
      fin.readBytes(payload.bytes.data(),payload.bytes.size());
      Ragdoll::State body;
      uint32_t count = 0;
      if(!payload.read(body.version) || !supportedVersion(body.version) || !payload.readString(body.skeleton) ||
         !payload.readMatrix(body.rootTransform) || !payload.read(count) ||
         count==0 || count>Resources::MAX_NUM_SKELETAL_NODES)
        return false;
      const size_t remaining = size-payload.offset;
      const size_t expectedParts = size_t(count)*partBytes(body.version);
      if(body.version<3 ? remaining!=expectedParts : remaining<expectedParts+sizeof(uint8_t)+sizeof(uint32_t))
        return false;
      body.parts.resize(count);
      for(auto& part:body.parts) {
        uint8_t sleeping = 0;
        if(!payload.read(part.bone) || !payload.readMatrix(part.transform) ||
           (body.version>=2 && !payload.readMatrix(part.referenceTransform)) ||
           (body.version>=3 && (!payload.readVector(part.centerOfMassMeters) ||
                               !payload.readQuaternion(part.rotation) ||
                               !payload.readVector(part.linearVelocityMeters))) ||
           !payload.readVector(part.linearVelocity) || !payload.readVector(part.angularVelocity) ||
           !payload.read(sleeping) || sleeping>1)
          return false;
        part.sleeping = sleeping!=0;
        if(body.version==1)
          part.referenceTransform = part.transform;
        }
      if(body.version>=3) {
        uint32_t ancestors = 0;
        uint8_t legacyAncestors = 0;
        if(!payload.read(legacyAncestors) || legacyAncestors>1 || !payload.read(ancestors) ||
           ancestors>Resources::MAX_NUM_SKELETAL_NODES || (legacyAncestors!=0 && ancestors!=0) ||
           size-payload.offset!=size_t(ancestors)*ancestorBytes)
          return false;
        body.legacyAncestors = legacyAncestors!=0;
        body.ancestors.resize(ancestors);
        for(auto& ancestor:body.ancestors)
          if(!payload.read(ancestor.bone) || !payload.read(ancestor.anchorBone) ||
             !payload.readMatrix(ancestor.referenceTransform))
            return false;
        }
      if(payload.offset!=payload.bytes.size() || !validState(body))
        return false;
      result.body = std::move(body);
      }
    if(!entryExhausted(fin))
      return false;
    snapshot = std::move(result);
    return true;
    }
  catch(const std::exception&) {
    return false;
    }
  }
