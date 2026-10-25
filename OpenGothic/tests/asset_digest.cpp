#include "basemesh.h"
#include "commandline.h"
#include "gothicglb.h"
#include "graphics/material.h"
#include "graphics/shaders.h"
#include "graphics/mesh/submesh/packedmesh.h"
#include "physics/collisionworld.h"
#include "physics/physicvbo.h"
#include "physics/worldcloth.h"
#include "resources.h"
#include "worldbase.h"

#include <Tempest/VulkanApi>
#include <zenkit/Stream.hh>
#include <zenkit/World.hh>
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/evp.h>
#endif
#include <array>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <iostream>
#include <stdexcept>
#include <type_traits>

namespace fs = std::filesystem;

namespace {
// Hash values, never object padding, pointers, container storage or SIMD's spare lane.
// GPU arrays include every allocated meshlet slot, including their explicit padding.
class Digest {
  public:
    Digest() {
#ifdef __APPLE__
      CC_SHA256_Init(&context);
#else
      if(!context || EVP_DigestInit_ex(context,EVP_sha256(),nullptr)!=1)
        throw std::runtime_error("SHA-256 initialization failed");
#endif
      }
    ~Digest() {
#ifndef __APPLE__
      EVP_MD_CTX_free(context);
#endif
      }
    Digest(const Digest&)=delete;
    Digest& operator=(const Digest&)=delete;

    void bytes(const void* data, size_t size) {
      if(size==0) return;
#ifdef __APPLE__
      CC_SHA256_Update(&context,data,CC_LONG(size));
#else
      if(EVP_DigestUpdate(context,data,size)!=1)
        throw std::runtime_error("SHA-256 update failed");
#endif
      }
    template<class T> void value(const T& v) {
      static_assert(std::is_arithmetic_v<T> || std::is_enum_v<T>);
      bytes(&v,sizeof(v));
      }
    void text(const std::string& s) {
      value(uint64_t(s.size())); bytes(s.data(),s.size());
      }
    template<class T> void array(const std::vector<T>& v) {
      static_assert(std::is_trivially_copyable_v<T>);
      value(uint64_t(v.size())); bytes(v.data(),v.size()*sizeof(T));
      }
    template<class T> void point(const T& p) {
      value(p.x); value(p.y); value(p.z);
      }
    std::string finish() {
      std::array<unsigned char,32> result{};
#ifdef __APPLE__
      CC_SHA256_Final(result.data(),&context);
#else
      unsigned int size=0;
      if(EVP_DigestFinal_ex(context,result.data(),&size)!=1 || size!=result.size())
        throw std::runtime_error("SHA-256 finalization failed");
#endif
      std::string hex;
      for(auto byte:result) {
        hex.push_back("0123456789abcdef"[byte>>4]);
        hex.push_back("0123456789abcdef"[byte&15]);
        }
      return hex;
      }
  private:
#ifdef __APPLE__
    CC_SHA256_CTX context{};
#else
    EVP_MD_CTX* context=EVP_MD_CTX_new();
#endif
  };
}

struct AssetDigest {
  Digest total;
  size_t rows=0;

  void emit(const std::string& kind, const std::string& name, Digest& digest) {
    const auto line=kind+" "+name+" "+digest.finish()+"\n";
    total.bytes(line.data(),line.size());
    std::fwrite(line.data(),1,line.size(),stdout);
    ++rows;
    }

  static void packed(Digest& d, const PackedMesh& p) {
    static_assert(sizeof(Resources::Vertex)==9*sizeof(uint32_t));
    d.array(p.vertices); d.array(p.indices); d.array(p.indices8);
    d.value(p.isUsingAlphaTest);
    d.value(uint64_t(p.subMeshes.size()));
    for(const auto& s:p.subMeshes) {
      d.text(gothicglb::materialJson(s.material,0));
      d.value(uint64_t(s.iboOffset)); d.value(uint64_t(s.iboLength));
      d.value(s.alphaTest); d.value(s.alphaTestRequired);
      }
    d.value(uint64_t(p.meshletBounds.size()));
    for(const auto& b:p.meshletBounds) { d.point(b.pos); d.value(b.r); }
    d.point(p.bbox().first); d.point(p.bbox().second);
    }

  static void materials(Digest& d, const PackedMesh& p) {
    d.value(uint64_t(p.subMeshes.size()));
    for(const auto& s:p.subMeshes) {
      const Material m(s.material,s.alphaTest,s.alphaTestRequired);
      d.value(m.alpha); d.value(m.sourceMaterialGroup); d.value(m.sourceAlphaFunc);
      d.value(m.sourceColorAlpha); d.value(m.alphaTestEnabled); d.value(m.alphaTestRequired);
      d.value(m.classifiedAlpha); d.value(m.alphaWeight); d.value(m.isGhost);
      d.value(m.texAniMapDirPeriod.x); d.value(m.texAniMapDirPeriod.y); d.value(m.texAniFPSInv);
      d.value(m.waveMaxAmplitude); d.value(m.envMapping); d.value(m.uvScale);
      d.value(m.antitileCell); d.value(m.antitile); d.value(m.materialMaps);
      d.value(uint64_t(m.frames.size()));
      d.value(m.tex!=nullptr);
      if(m.tex) { d.value(m.tex->format()); d.value(m.tex->w()); d.value(m.tex->h()); }
      d.value(m.normal!=nullptr); d.value(m.orm!=nullptr);
      d.value(m.isSolid()); d.value(m.hasFrameAnimation()); d.value(m.hasUvAnimation());
      d.value(m.isTesselated()); d.value(m.isForwardShading()); d.value(m.isSceneInfoRequired());
      d.value(m.isShadowmapRequired()); d.value(m.isTextureInShadowPass());
      }
    }

  static void physics(Digest& d, const PhysicVbo& p) {
    d.value(uint64_t(p.vert.size()));
    for(const auto& v:p.vert)
      for(JPH::uint axis=0;axis<3;++axis) d.value(v[axis]);
    d.array(p.id);
    d.value(uint64_t(p.segments.size()));
    for(const auto& s:p.segments) {
      d.value(uint64_t(s.off)); d.value(uint64_t(s.size)); d.value(s.mat);
      d.text(s.sector ? s.sector : "");
      }
    }

  static void joltInput(Digest& d, const PackedMesh& p) {
    std::vector<JPH::Vec3> vertices;
    for(const auto& v:p.vertices)
      vertices.push_back(CollisionWorld::toMeters(Tempest::Vec3(v.pos[0],v.pos[1],v.pos[2])));
    PhysicVbo land(&vertices),water(&vertices);
    for(const auto& s:p.subMeshes) {
      if(s.material.disable_collision || s.iboLength==0) continue;
      if(s.material.group==zenkit::MaterialGroup::WATER)
        water.addIndex(p.indices,s.iboOffset,s.iboLength,s.material.group);
      else
        land.addIndex(p.indices,s.iboOffset,s.iboLength,s.material.group,s.material.name.c_str());
      }
    physics(d,land); physics(d,water);
    }

  void world(zenkit::Mesh mesh, const std::string& name, const std::string& path) {
    const auto cloth=WorldCloth::extract(mesh);
    const PackedMesh visual(mesh,PackedMesh::PK_VisualLnd);
    Digest gpu,mat,phys,clothDigest;
    packed(gpu,visual); materials(mat,visual);
    const PackedMesh collision(mesh,PackedMesh::PK_Physic);
    packed(phys,collision); joltInput(phys,collision);
    clothDigest.value(uint64_t(cloth.surfaces.size()));
    // The fallback and each simulated surface have their own GPU packing.
    if(!cloth.original.polygons.material_indices.empty()) {
      packed(clothDigest,PackedMesh(cloth.original,PackedMesh::PK_VisualLnd));
      const PackedMesh fallback(cloth.original,PackedMesh::PK_Physic);
      packed(clothDigest,fallback);
      std::vector<JPH::Vec3> vertices;
      for(const auto& v:fallback.vertices)
        vertices.push_back(CollisionWorld::toMeters(Tempest::Vec3(v.pos[0],v.pos[1],v.pos[2])));
      PhysicVbo input(&vertices);
      for(const auto& s:fallback.subMeshes)
        if(!s.material.disable_collision && s.iboLength>0)
          input.addIndex(fallback.indices,s.iboOffset,s.iboLength,s.material.group);
      physics(clothDigest,input);
      }
    for(const auto& s:cloth.surfaces) {
      clothDigest.value(s.id); clothDigest.text(s.visual); clothDigest.array(s.pins);
      clothDigest.value(s.stiffness); clothDigest.value(s.linearDamping);
      const PackedMesh surface(s.mesh,PackedMesh::PK_VisualLnd);
      packed(clothDigest,surface); materials(clothDigest,surface);
      // Cloth simulation consumes source positions and triangle indices, before packing.
      clothDigest.value(uint64_t(s.mesh.vertices.size()));
      for(const auto& v:s.mesh.vertices) clothDigest.point(v);
      clothDigest.array(s.mesh.polygons.vertex_indices);
      }
    emit("world-gpu-"+path,name,gpu); emit("world-physics-"+path,name,phys);
    emit("world-material-"+path,name,mat); emit("world-cloth-"+path,name,clothDigest);
    }

  void mesh(const zenkit::MultiResolutionMesh& mesh, const std::string& name, const std::string& path) {
    PackedMesh p(mesh,PackedMesh::PK_Visual);
    Digest gpu,mat,phys;
    packed(gpu,p); materials(mat,p);
    const PhysicVbo vbo(std::move(p));
    physics(phys,vbo);
    emit("mesh-gpu-"+path,name,gpu); emit("mesh-physics-"+path,name,phys);
    emit("mesh-material-"+path,name,mat);
    }

  void run(const fs::path& base, Tempest::Device& device) {
    // Each path gets fresh texture caches, so Material sees its actual decoded format.
    for(const bool useBase:{false,true}) {
      setenv("OPENGOTHIC_BASE",useBase ? base.c_str() : "",1);
      Resources resources(device);
      Shaders shaders;
      shaders.waitCompiler();
      Resources::loadVdfs({},true);
      const std::string path=useBase ? "base" : "archive";
      for(const auto* name:{"NEWWORLD","OLDWORLD","ADDONWORLD","DRAGONISLAND"}) {
        const auto key=std::string(name)+".ZEN";
        if(useBase) {
          zenkit::World loaded;
          std::vector<WorldData::Room> rooms;
          const auto result=tryWorldData(loaded,rooms,key,base/"worlds"/name,{},resources.mountedArchives);
          if(!result.replaced) throw std::runtime_error(key+": "+result.error);
          world(std::move(loaded.world_mesh),key,path);
          } else {
          auto input=Resources::getFileBuffer(key);
          zenkit::World loaded;
          loaded.load(input.get(),zenkit::GameVersion::GOTHIC_2);
          world(std::move(loaded.world_mesh),key,path);
          }
        }
      // Use the converted index on both paths; require the archive loader to contain every entry.
      Resources::BaseIndex index;
      index.load(base,"meshes"); index.validate(resources.mountedArchives);
      if(!index.enabled || index.files.empty()) throw std::runtime_error("Mesh base index is unavailable");
      const std::map<std::string,std::string> files(index.files.begin(),index.files.end());
      for(const auto& [key,file]:files) {
        if(useBase) {
          mesh(readBaseMesh(base/"meshes"/file),key+".MRM",path);
          } else {
          auto input=Resources::getFileBuffer(key+".MRM");
          zenkit::MultiResolutionMesh loaded;
          loaded.load(input.get());
          mesh(loaded,key+".MRM",path);
          }
        }
      }
    const auto hash=total.finish();
    std::printf("total %zu %s\n",rows,hash.c_str());
    }
  };

int main() {
  try {
    // Tempest logs use cout; only digest records use the C stdout stream.
    std::cout.rdbuf(std::cerr.rdbuf());
    const auto* game=std::getenv("GOTHIC_DIR");
    const auto* dir=std::getenv("OPENGOTHIC_BASE");
    if(!game || !*game || !dir || !*dir)
      throw std::runtime_error("GOTHIC_DIR and OPENGOTHIC_BASE must be set");
    const auto base=fs::absolute(dir);
    setenv("OPENGOTHIC_CONTENT","",1); setenv("OPENGOTHIC_TEXTURES","",1);
    const char* args[]={"gothic-asset-digest","-g",game};
    CommandLine commandLine(3,args);
    Tempest::VulkanApi api;
    Tempest::Device device(api);
    AssetDigest digest;
    digest.run(base,device);
    return 0;
    }
  catch(const std::exception& error) {
    std::fprintf(stderr,"asset digest: %s\n",error.what());
    return 1;
    }
  }
