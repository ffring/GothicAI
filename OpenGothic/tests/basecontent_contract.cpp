#include "asset/fromzenkit.h"
#include "basemesh.h"
#include "commandline.h"
#include "gltfmesh.h"
#include "gothicglb.h"
#include "graphics/mesh/submesh/packedmesh.h"
#include "physics/physicvbo.h"
#include "resources.h"

#include <zenkit/Stream.hh>
#include <zenkit/MultiResolutionMesh.hh>
#include <zenkit/Texture.hh>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <bit>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <stdexcept>

namespace fs = std::filesystem;

struct BaseContentContract {
  static void require(bool condition, const std::string& message) {
    if(!condition)
      throw std::runtime_error(message);
    }

  template<class A, class B>
  static void equal(A a, B b, const std::string& field) {
    require(a==b,field+": "+std::to_string(a)+" vs "+std::to_string(b));
    }

  static void bytes(const void* a, const void* b, size_t size, const std::string& field) {
    const auto* x = static_cast<const uint8_t*>(a);
    const auto* y = static_cast<const uint8_t*>(b);
    for(size_t i=0;i<size;++i)
      if(x[i]!=y[i])
        equal(x[i],y[i],field+" byte "+std::to_string(i));
    }

  template<class T>
  static void vector(const std::vector<T>& a, const std::vector<T>& b, const std::string& field) {
    equal(a.size(),b.size(),field+" count");
    bytes(a.data(),b.data(),a.size()*sizeof(T),field);
    }

  static void point(const Tempest::Vec3& a, const Tempest::Vec3& b, const std::string& field) {
    equal(std::bit_cast<uint32_t>(a.x),std::bit_cast<uint32_t>(b.x),field+" x bits");
    equal(std::bit_cast<uint32_t>(a.y),std::bit_cast<uint32_t>(b.y),field+" y bits");
    equal(std::bit_cast<uint32_t>(a.z),std::bit_cast<uint32_t>(b.z),field+" z bits");
    }

  static void texture(const Resources::TextureData& a, const Resources::TextureData& b, const std::string& name) {
    equal(int(a.pixmap.format()),int(b.pixmap.format()),name+" format");
    equal(a.pixmap.w(),b.pixmap.w(),name+" width");
    equal(a.pixmap.h(),b.pixmap.h(),name+" height");
    equal(a.pixmap.mipCount(),b.pixmap.mipCount(),name+" mips");
    equal(a.useMipmap,b.useMipmap,name+" GPU mip flag");
    equal(a.pixmap.dataSize(),b.pixmap.dataSize(),name+" data size");
    bytes(a.pixmap.data(),b.pixmap.data(),a.pixmap.dataSize(),name);
    }

  static void packed(PackedMesh& a, PackedMesh& b, const std::string& name) {
    static_assert(sizeof(Resources::Vertex)==9*sizeof(uint32_t));
    vector(a.vertices,b.vertices,name+" vertices");
    vector(a.indices,b.indices,name+" indices");
    vector(a.indices8,b.indices8,name+" meshlet byte indices");
    const auto meshlets=a.indices.size()/PackedMesh::MaxInd;
    equal(a.vertices.size(),meshlets*PackedMesh::MaxVert,name+" meshlet vertex slots");
    equal(a.indices.size(),meshlets*PackedMesh::MaxInd,name+" meshlet index slots");
    equal(a.indices8.size(),meshlets*PackedMesh::MaxPrim*4,name+" meshlet byte slots");
    equal(a.isUsingAlphaTest,b.isUsingAlphaTest,name+" alpha test");
    equal(a.subMeshes.size(),b.subMeshes.size(),name+" submeshes");
    for(size_t i=0;i<a.subMeshes.size();++i) {
      const auto& x=a.subMeshes[i];
      const auto& y=b.subMeshes[i];
      const auto where=name+" submesh "+std::to_string(i);
      require(gothicglb::sameMaterial(x.material,y.material),where+" material differs");
      equal(x.iboOffset,y.iboOffset,where+" offset");
      equal(x.iboLength,y.iboLength,where+" length");
      equal(x.alphaTest,y.alphaTest,where+" alpha test");
      equal(x.alphaTestRequired,y.alphaTestRequired,where+" alpha test required");
      }
    equal(a.meshletBounds.size(),b.meshletBounds.size(),name+" meshlets");
    for(size_t i=0;i<a.meshletBounds.size();++i) {
      const auto where=name+" meshlet "+std::to_string(i);
      point(a.meshletBounds[i].pos,b.meshletBounds[i].pos,where+" center");
      equal(std::bit_cast<uint32_t>(a.meshletBounds[i].r),std::bit_cast<uint32_t>(b.meshletBounds[i].r),where+" radius bits");
      }
    point(a.bbox().first,b.bbox().first,name+" bbox min");
    point(a.bbox().second,b.bbox().second,name+" bbox max");
    // Static objects feed this same PK_Visual packing into PhysicMeshShape.
    PhysicVbo x(std::move(a)),y(std::move(b));
    equal(x.vert.size(),y.vert.size(),name+" physics vertices");
    for(size_t i=0;i<x.vert.size();++i)
      for(JPH::uint axis=0;axis<3;++axis)
        equal(std::bit_cast<uint32_t>(x.vert[i][axis]),std::bit_cast<uint32_t>(y.vert[i][axis]),
              name+" physics vertex "+std::to_string(i)+" axis "+std::to_string(axis)+" bits");
    vector(x.id,y.id,name+" physics indices");
    equal(x.segments.size(),y.segments.size(),name+" physics segments");
    for(size_t i=0;i<x.segments.size();++i) {
      const auto where=name+" physics segment "+std::to_string(i);
      equal(x.segments[i].off,y.segments[i].off,where+" offset");
      equal(x.segments[i].size,y.segments[i].size,where+" size");
      equal(int(x.segments[i].mat),int(y.segments[i].mat),where+" material");
      }
    }

  static rapidjson::Document json(const fs::path& file) {
    const auto data=gothicglb::readFile(file);
    rapidjson::Document result;
    result.Parse(reinterpret_cast<const char*>(data.data()),data.size());
    require(!result.HasParseError() && result.IsObject(),"invalid JSON: "+file.string());
    return result;
    }

  static void writeJson(const fs::path& file, const rapidjson::Document& data) {
    rapidjson::StringBuffer text;
    rapidjson::Writer<rapidjson::StringBuffer> writer(text);
    data.Accept(writer);
    gothicglb::writeFile(file,text.GetString(),text.GetSize());
    }

  static void ownMesh(const fs::path& file) {
    gothicglb::GlbBuilder glb;
    glb.accessor(std::vector<float>{1,2,3,4,2,3,1,6,3},5126,"VEC3",3,false,34962,
                 R"(,"min":[1,2,3],"max":[4,6,3])");
    glb.accessor(std::vector<float>{0,0,1,0,0,1,0,0,1},5126,"VEC3",3,false,34962);
    glb.accessor(std::vector<float>{0,0,1,0,0,1},5126,"VEC2",2,false,34962);
    glb.accessor(std::vector<uint16_t>{0,1,2},5123,"SCALAR",1,false,34963);
    const auto data=glb.finish(
      R"({"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[0]}],"nodes":[{"mesh":0}],)"
      R"("materials":[{"name":"OWN_CONTRACT","alphaMode":"OPAQUE"}],)"
      R"("meshes":[{"primitives":[{"attributes":{"POSITION":0,"NORMAL":1,"TEXCOORD_0":2},"indices":3,"material":0}]}],)"
      "\"buffers\":[{\"byteLength\":"+std::to_string(glb.bin.size())+"}],\"bufferViews\":"+glb.viewsJson()+
      ",\"accessors\":"+glb.accessorsJson()+"}");
    gothicglb::writeFile(file,data.data(),data.size());
    }

  static void census(const fs::path& file, size_t meshes) {
    const auto data=json(file);
    equal(data["replacements"].Size(),meshes,"census base mesh count");
    for(const auto& row:data["replacements"].GetArray()) {
      require(std::string(row["kind"].GetString())=="basemesh","base mesh census kind");
      require(std::string(row["replacement"].GetString()).starts_with("base/meshes/"),"base mesh census path");
      }
    }

  static void run(const fs::path& base, const fs::path& temporary) {
    setenv("OPENGOTHIC_CONTENT","",1);
    setenv("OPENGOTHIC_TEXTURES","",1);
    const auto censusPath=temporary/"census.json";
    setenv("OPENGOTHIC_CENSUS",censusPath.c_str(),1);
    std::string meshKey;
    size_t textureCount=0,meshCount=0,dds=0,png=0,tga=0,vertices=0,indices=0,meshlets=0;
    {
    Resources resources;
    require(!resources.baseTextures.enabled && !resources.baseMeshes.enabled,"base must wait for mounted archives");
    Resources::loadVdfs({},true);
    require(resources.baseTextures.enabled && resources.baseMeshes.enabled,"matching archives must enable both indices");
    require(!resources.baseTextures.files.empty() && !resources.baseMeshes.files.empty(),"both indices must contain files");
    for(const auto& [key,file]:resources.baseTextures.files) {
      auto* entry=Resources::vdfsIndex().find(key+"-C.TEX");
      const bool compiled=entry!=nullptr;
      if(!compiled)
        entry=Resources::vdfsIndex().find(key+".TGA");
      require(entry!=nullptr,key+": source texture missing");
      for(const bool forceMips:{false,true}) {
        auto input=entry->open_read();
        Resources::TextureData original;
        if(compiled) {
          zenkit::Texture tex;
          tex.load(input.get());
          original=Resources::decodeTexture(tex,forceMips);
          } else {
          original=Resources::decodeTexture(*input,forceMips);
          }
        const auto converted=Resources::decodeBaseTexture(base/"textures"/file,forceMips);
        texture(original,converted,key+" forceMips="+std::to_string(forceMips));
        }
      dds+=fs::path(file).extension()==".dds";
      png+=fs::path(file).extension()==".png";
      tga+=fs::path(file).extension()==".tga";
      ++textureCount;
      }
    std::printf("Textures: %zu equal (%zu DDS, %zu PNG, %zu TGA), both GPU mip flags\n",textureCount,dds,png,tga);
    for(const auto& [key,file]:resources.baseMeshes.files) {
      auto input=Resources::getFileBuffer(key+".MRM");
      zenkit::MultiResolutionMesh archiveMesh;
      archiveMesh.load(input.get());
      const auto original=asset::fromZenKit(archiveMesh);
      const auto converted=readBaseMesh(base/"meshes"/file);
      auto difference=compareBaseMeshes(original,converted);
      require(difference.empty(),key+": "+difference);
      bool replaced=true;
      const auto loaded=Resources::loadVisualMesh(key+".3ds",&replaced);
      difference=compareBaseMeshes(converted,loaded);
      require(difference.empty(),key+" selected: "+difference);
      require(!replaced && !Resources::meshReplacementLoaded(key),key+": base must retain original alpha/fracture semantics");
      PackedMesh a(original,PackedMesh::PK_Visual),b(loaded,PackedMesh::PK_Visual);
      vertices+=a.vertices.size();
      indices+=a.indices.size();
      meshlets+=a.indices.size()/PackedMesh::MaxInd;
      packed(a,b,key);
      ++meshCount;
      }
    meshKey=resources.baseMeshes.files.begin()->first;
    }
    census(censusPath,meshCount);
    std::printf("Meshes: %zu equal; %zu vertices, %zu indices, %zu meshlets; physics packing equal\n",
                meshCount,vertices,indices,meshlets);

    const auto invalid=temporary/"invalid";
    for(const auto* kind:{"textures","meshes"}) {
      fs::create_directories(invalid/kind);
      auto index=json(base/kind/(std::string(kind)+".json"));
      auto& size=index["archives"][0]["size"];
      const auto old=size.GetUint64();
      size.SetUint64(old+1);
      writeJson(invalid/kind/(std::string(kind)+".json"),index);
      std::printf("Reject %s archive size: %llu -> %llu\n",kind,
                  static_cast<unsigned long long>(old),static_cast<unsigned long long>(old+1));
      }
    setenv("OPENGOTHIC_BASE",invalid.c_str(),1);
    {
    Resources resources;
    Resources::loadVdfs({},true);
    require(!resources.baseTextures.enabled && !resources.baseMeshes.enabled,"wrong archive sizes must disable both bases");
    auto input=Resources::getFileBuffer(meshKey+".MRM");
    zenkit::MultiResolutionMesh archiveMesh;
    archiveMesh.load(input.get());
    const auto original=asset::fromZenKit(archiveMesh);
    require(compareBaseMeshes(original,Resources::loadVisualMesh(meshKey+".MRM")).empty(),"disabled base must fall back to archive");
    }
    equal(json(censusPath)["replacements"].Size(),0u,"disabled base census replacements");

    const auto content=temporary/"content";
    fs::create_directories(content/"meshes");
    ownMesh(content/"meshes/own.glb");
    const auto own=loadGltfMesh(content/"meshes/own.glb");
    const auto index="{\""+meshKey+"\":{\"glb\":\"own.glb\"}}";
    gothicglb::writeFile(content/"meshes/meshes.json",index.data(),index.size());
    setenv("OPENGOTHIC_BASE",base.c_str(),1);
    setenv("OPENGOTHIC_CONTENT",content.c_str(),1);
    {
    Resources resources;
    Resources::loadVdfs({},true);
    require(resources.baseMeshes.enabled,"priority check requires enabled base");
    bool replaced=false;
    const auto loaded=Resources::loadVisualMesh(meshKey+".MRM",&replaced);
    require(replaced && Resources::meshReplacementLoaded(meshKey),"own mesh must win over base");
    require(compareBaseMeshes(own,loaded).empty(),"own mesh selection differs");
    }
    const auto selected=json(censusPath);
    equal(selected["files"].Size(),0u,"own replacement archive opens");
    equal(selected["replacements"].Size(),1u,"own replacement count");
    require(std::string(selected["replacements"][0]["kind"].GetString())=="mesh","own replacement census kind");
    std::puts("Selection: archive mismatch falls back; own content wins over enabled base; census kinds stay separate");
    }
  };

int main() {
  const auto temporary=fs::temp_directory_path()/
    ("gothic-basecontent-contract-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  int result=0;
  try {
    const auto* gothic=std::getenv("GOTHIC_DIR");
    const auto* dir=std::getenv("OPENGOTHIC_BASE");
    BaseContentContract::require(gothic && *gothic,"GOTHIC_DIR must point to a Gothic II installation");
    BaseContentContract::require(dir && *dir,"OPENGOTHIC_BASE must point to a content-convert base with textures and meshes");
    const auto base=fs::absolute(dir);
    fs::create_directories(temporary);
    const char* args[]={"gothic-basecontent-contract","-g",gothic};
    CommandLine commandLine(3,args);
    BaseContentContract::run(base,temporary);
    std::puts("base content contract: passed");
    }
  catch(const std::exception& error) {
    std::fprintf(stderr,"base content contract: %s\n",error.what());
    result=1;
    }
  std::error_code error;
  fs::remove_all(temporary,error);
  return result;
  }
