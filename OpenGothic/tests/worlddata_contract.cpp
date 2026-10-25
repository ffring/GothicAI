#include "asset/fromzenkit.h"
#include <zenkit/World.hh>
#include "commandline.h"
#include "gothic.h"
#include "resources.h"
#include "worldbase.h"
#include "world/world.h"
#include "world/objects/interactive.h"
#include "world/objects/item.h"
#include "world/objects/npc.h"
#include "game/gamesession.h"
#include "gamemusic.h"
#include "graphics/mesh/protomesh.h"
#include "utils/workers.h"

#include <rapidjson/document.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>
#include <typeinfo>

namespace fs = std::filesystem;

struct WorldDataContract {
  using Rows = std::vector<std::pair<std::string,std::string>>;
  static void require(bool ok, const std::string& message) {
    if(!ok) throw std::runtime_error(message);
    }
  template<class T> static void field(Rows& rows, const std::string& key, const T& value) {
    std::ostringstream text;
    text << std::setprecision(9) << value;
    rows.emplace_back(key,text.str());
    }
  static void vector(Rows& rows, const std::string& key, const Tempest::Vec3& v) {
    field(rows,key+".x",v.x); field(rows,key+".y",v.y); field(rows,key+".z",v.z);
    }
  static void compare(const Rows& a, const Rows& b, const std::string& world) {
    require(a.size()==b.size(),world+": runtime field count "+std::to_string(a.size())+" vs "+std::to_string(b.size()));
    for(size_t i=0;i<a.size();++i)
      require(a[i]==b[i],world+": "+a[i].first+" ZEN="+a[i].second+" base="+b[i].second+" ("+b[i].first+")");
    }
  static void vob(Rows& rows, const Vob& v, const std::string& key, std::map<int,size_t>& counts) {
    ++counts[int(v.vobType)];
    field(rows,key+".class",typeid(v).name()); field(rows,key+".type",int(v.vobType));
    field(rows,key+".id",v.vobObjectID);
    Vob::AttachmentSnapshot attachment;
    v.fillAttachmentSnapshot(attachment);
    field(rows,key+".visual",attachment.visual);
    field(rows,key+".parent",attachment.parentZenId);
    for(size_t i=0;i<16;++i) {
      field(rows,key+".transform["+std::to_string(i)+"]",v.transform().data()[i]);
      field(rows,key+".local["+std::to_string(i)+"]",v.localTransform().data()[i]);
      }
    if(const auto* mob=dynamic_cast<const Interactive*>(&v)) {
      field(rows,key+".name",mob->vobNameStr()); field(rows,key+".state",mob->stateId());
      const auto* mesh=mob->objVisual().protoMesh();
      field(rows,key+".mesh",mesh ? mesh->fname : "");
      }
    if(const auto* trigger=dynamic_cast<const AbstractTrigger*>(&v))
      field(rows,key+".name",trigger->name());
    field(rows,key+".children",v.child.size());
    for(size_t i=0;i<v.child.size();++i)
      vob(rows,*v.child[i],key+"/"+std::to_string(i),counts);
    }
  static Rows snapshot(World& world, const WorldData::Data& original) {
    Rows rows;
    auto& obj=world.wobj;
    field(rows,"roots",obj.rootVobs.size());
    field(rows,"statics",obj.objStatic.size());
    field(rows,"interactives",obj.mobsiCount());
    field(rows,"items",obj.itmCount()); field(rows,"npcs",obj.npcCount());
    field(rows,"triggers",obj.triggers.size()); field(rows,"collisionZones",obj.collisionZn.size());
    std::map<int,size_t> counts;
    for(size_t i=0;i<obj.rootVobs.size();++i)
      vob(rows,*obj.rootVobs[i],"vob["+std::to_string(i)+"]",counts);
    for(auto [type,count]:counts) field(rows,"count[type="+std::to_string(type)+"]",count);
    for(size_t i=0;i<obj.itmCount();++i) {
      const auto& item=obj.itm(i);
      field(rows,"item["+std::to_string(i)+"].class",item.clsId());
      vector(rows,"item["+std::to_string(i)+"].position",item.position());
      }
    auto& matrix=*world.wmatrix;
    std::map<const WayPoint*,std::string> indices;
    const auto points=[&](const auto& list,const std::string& kind) {
      field(rows,kind+".size",list.size());
      for(size_t i=0;i<list.size();++i) {
        const auto& p=list[i]; const auto key=kind+"["+std::to_string(i)+"]";
        indices.emplace(&p,key);
        field(rows,key+".name",p.name); field(rows,key+".underwater",p.underWater);
        field(rows,key+".free",p.freePoint);
        vector(rows,key+".pos",p.pos); vector(rows,key+".ground",p.groundPos); vector(rows,key+".dir",p.dir);
        }
      };
    points(matrix.wayPoints,"waypoint"); points(matrix.freePoints,"freepoint"); points(matrix.startPoints,"startpoint");
    for(size_t i=0;i<matrix.edges.size();++i) {
      field(rows,"edge["+std::to_string(i)+"].a",matrix.edges[i].a);
      field(rows,"edge["+std::to_string(i)+"].b",matrix.edges[i].b);
      }
    for(size_t i=0;i<matrix.indexPoints.size();++i)
      field(rows,"index["+std::to_string(i)+"]",indices.at(matrix.indexPoints[i]));
    for(const auto& p:matrix.wayPoints)
      for(size_t i=0;i<p.connections().size();++i) {
        const auto key=indices.at(&p)+".connection["+std::to_string(i)+"]";
        field(rows,key+".to",indices.at(p.connections()[i].point));
        field(rows,key+".length",p.connections()[i].len);
        }
    auto bsp=original;
    bsp.bsp.nodes=world.bsp.nodes; bsp.bsp.sectors=world.bsp.sectors;
    bsp.bsp.leaf_node_indices=world.bsp.leaf_node_indices;
    const auto diff=WorldData::compare(original,bsp);
    require(diff.empty(),std::string(world.name())+": runtime BSP: "+diff);
    const auto bounds=original.bsp.nodes.empty() ? original.bbox : original.bsp.nodes.front().bbox;
    const auto nx=size_t(std::ceil(double(bounds.max.x-bounds.min.x)/400.));
    const auto nz=size_t(std::ceil(double(bounds.max.z-bounds.min.z)/400.));
    size_t samples=0,indoor=0;
    for(size_t x=0;x<=nx;++x) for(size_t z=0;z<=nz;++z) for(unsigned h=0;h<3;++h) {
      const zenkit::Vec3 p{bounds.min.x+(bounds.max.x-bounds.min.x)*float(double(x)/double(std::max(size_t(1),nx))),
                           bounds.min.y+(bounds.max.y-bounds.min.y)*float(h+1)/4.f,
                           bounds.min.z+(bounds.max.z-bounds.min.z)*float(double(z)/double(std::max(size_t(1),nz)))};
      const bool expected=WorldData::isIndoor(original.bsp.mode,original.rooms,p);
      const bool actual=world.isIndoor({p.x,p.y,p.z});
      require(expected==actual,std::string(world.name())+": isIndoor at "+std::to_string(p.x)+","+
              std::to_string(p.y)+","+std::to_string(p.z)+" ZEN="+std::to_string(expected)+" base="+std::to_string(actual));
      ++samples; indoor+=actual;
      }
    const auto key=NavMesh::cacheKey(world.physic()->navigationGeometry(),NavMesh::Params{},std::string(world.name()));
    field(rows,"navmesh.key",key);
    std::printf("%s: runtime %zu roots, %zu static, %zu interactive, %zu items, %zu NPCs, %zu types; %zu points, %zu edges; %zu indoor samples (%zu inside); nav key %llu\n",
                std::string(world.name()).c_str(),obj.rootVobs.size(),obj.objStatic.size(),obj.mobsiCount(),obj.itmCount(),obj.npcCount(),counts.size(),
                matrix.wayPoints.size(),matrix.edges.size(),samples,indoor,static_cast<unsigned long long>(key));
    return rows;
    }
  static rapidjson::Document json(const fs::path& path) {
    std::ifstream in(path); require(bool(in),"cannot read "+path.string());
    const std::string text{std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
    rapidjson::Document doc; doc.Parse(text.c_str()); require(!doc.HasParseError(),"invalid "+path.string()); return doc;
    }
  static uint64_t zenOpens(const fs::path& census) {
    require(Resources::writeCensus(),"cannot write census");
    const auto doc=json(census);
    uint64_t count=0;
    for(const auto& file:doc["files"].GetArray())
      if(std::string_view(file["name"].GetString()).ends_with(".ZEN"))
        count+=file["opens"].GetUint64();
    return count;
    }
  static void write(const fs::path& path, const rapidjson::Document& doc) {
    rapidjson::StringBuffer text; rapidjson::Writer<rapidjson::StringBuffer> writer(text); doc.Accept(writer);
    std::ofstream out(path); out << text.GetString(); require(bool(out),"cannot write "+path.string());
    }
  static void run(const fs::path& root, const fs::path& gothicDir) {
    const auto base=root/"base";
    const std::array<std::string,4> worlds={"NEWWORLD","OLDWORLD","ADDONWORLD","DRAGONISLAND"};
    const auto* supplied=std::getenv("OPENGOTHIC_BASE");
    require(supplied && *supplied,"OPENGOTHIC_BASE must point to build-base.sh output");
    fs::copy(supplied,base,fs::copy_options::recursive);
    const auto reference=root/"archive-reference";
    fs::create_directory(reference);
    for(const char* part:{"base.json","textures","meshes","zen"})
      fs::create_symlink(base/part,reference/part);
    setenv("OPENGOTHIC_CONTENT","",1); setenv("OPENGOTHIC_TEXTURES","",1);
    setenv("OPENGOTHIC_FRACTURE","",1); setenv("OPENGOTHIC_PHYSICS_THREADS","0",1);
    Workers::setSerialExecution(true);
    for(const auto& name:worlds) {
      const auto file=name+".ZEN";
      WorldData::Data original;
      Rows expected;
      for(const bool useBase:{false,true}) {
        setenv("OPENGOTHIC_BASE",base.c_str(),1);
        const auto census=root/(name+(useBase ? "-base.json" : "-zen.json"));
        setenv("OPENGOTHIC_CENSUS",census.c_str(),1);
        Resources resources;
        Gothic gothic;
        gothic.isNpcInDialogFn=[](const Npc*) { return false; };
        gothic.setupGlobalScripts();
        GameMusic music;
        WorldData::Data loaded;
        asset::WorldMesh loadedMesh;
        const auto manifest=readWorldChunkManifest(base/"worlds"/name);
        if(!useBase) {
          auto reader=Resources::vdfsIndex().find(file)->open_read();
          zenkit::World archive; archive.load(reader.get(),zenkit::GameVersion::GOTHIC_2);
          original=WorldData::fromWorld(archive,manifest.source);
          loadedMesh=asset::fromZenKit(archive.world_mesh);
          // Feed the same runtime consumers from an independently decoded archive
          // fixture. There is no archive selection mode in the engine any more.
          const auto dir=reference/"worlds"/name;
          writeWorldChunks(loadedMesh,manifest.source,dir);
          WorldData::write(original,dir);
          auto index=json(dir/"world.json");
          const auto sourceIndex=json(base/"worlds"/name/"world.json");
          rapidjson::Value archives;
          archives.CopyFrom(sourceIndex["archives"],index.GetAllocator());
          index.AddMember("archives",archives,index.GetAllocator());
          write(dir/"world.json",index);
          resources.automaticBase.path=reference;
          } else {
          Resources::loadWorld(loaded,loadedMesh,file);
          require(zenOpens(census)==0,file+": base loader opened ZEN");
          auto restored=WorldData::read(base/"worlds"/name);
          const auto diff=WorldData::compare(original,restored);
          require(diff.empty(),file+": "+diff);
          auto reader=Resources::vdfsIndex().find(file)->open_read(); zenkit::World zen;
          zen.load(reader.get(),zenkit::GameVersion::GOTHIC_2);
          const auto meshDiff=compareWorldMeshes(canonicalWorldMesh(asset::fromZenKit(zen.world_mesh)),canonicalWorldMesh(loadedMesh));
          require(meshDiff.empty(),file+": mesh "+meshDiff);
          }
        const auto before=zenOpens(census);
        gothic.setGame(std::make_unique<GameSession>(file));
        if(useBase)
          require(zenOpens(census)==before,file+": base GameSession opened ZEN");
        const auto actual=snapshot(*gothic.world(),original);
        if(useBase) compare(expected,actual,file); else expected=actual;
        gothic.clearGame().reset();
        }
      std::printf("%s: complete headless World / Vob::load / WorldObjects / WayMatrix equal (%zu fields); 0 engine ZEN opens with base\n",file.c_str(),expected.size());
      }
    // The actual selection function must reject a mismatched archive size,
    // and accept complete vob/waynet files from content ahead of the base.
    setenv("OPENGOTHIC_BASE",base.c_str(),1);
    setenv("OPENGOTHIC_CENSUS","",1);
    {
    Resources resources; Gothic gothic;
    const auto dir=base/"worlds/NEWWORLD";
    auto index=json(dir/"world.json");
    const auto size=index["archives"][0]["size"].GetUint64();
    index["archives"][0]["size"].SetUint64(size+1); write(dir/"world.json",index);
    WorldData::Data fallback;
    asset::WorldMesh fallbackMesh;
    bool rejected=false;
    try { Resources::loadWorld(fallback,fallbackMesh,"NEWWORLD.ZEN"); }
    catch(const BaseStore::Error&) { rejected=true; }
    require(rejected,"archive size +1 must reject the world");
    index["archives"][0]["size"].SetUint64(size); write(dir/"world.json",index);
    Resources::loadWorld(fallback,fallbackMesh,"NEWWORLD.ZEN");
    const auto content=root/"content"; const auto overrides=content/"worlds/NEWWORLD";
    fs::create_directories(overrides);
    auto vobs=json(dir/"vobs.json");
    vobs["vobs"].Clear(); write(overrides/"vobs.json",vobs);
    auto waynet=json(dir/"waynet.json");
    waynet["waynet"]["edges"].Clear(); write(overrides/"waynet.json",waynet);
    setenv("OPENGOTHIC_CONTENT",content.c_str(),1);
    WorldData::Data changed;
    asset::WorldMesh changedMesh;
    Resources::loadWorld(changed,changedMesh,"NEWWORLD.ZEN");
    require(changed.vobs.empty() && !fallback.vobs.empty(),"content vobs.json must replace the whole base tree");
    require(changed.waynet->edges.empty() && !fallback.waynet->edges.empty(),"content waynet.json must replace the whole base graph");
    const auto zenIndex=json(base/"zen/zen.json");
    size_t zenCount=0;
    for(const auto& entry:zenIndex["files"].GetObject()) {
      const auto name=std::string(entry.name.GetString());
      auto source=Resources::vdfsIndex().find(name)->open_read();
      source->seek(0,zenkit::Whence::END);
      std::vector<std::byte> bytes(source->tell()); source->seek(0,zenkit::Whence::BEG);
      require(source->read(bytes.data(),bytes.size())==bytes.size(),"incomplete archive ZEN");
      WorldData::ZenFile zen,converted;
      require(WorldData::loadZen(bytes,{name,bytes.size(),worldChunkCrc32(bytes.data(),bytes.size())},zen),"meshless ZEN expected");
      require(Resources::loadBaseZen(name,zen.kind,converted),"base ZEN selection failed: "+name);
      const auto diff=WorldData::compareZen(zen,converted); require(diff.empty(),name+": "+diff); ++zenCount;
      }
    std::printf("Selection: archive size %llu -> %llu rejected; content vobs/waynet win; %zu base ZEN files equal\n",
                static_cast<unsigned long long>(size),static_cast<unsigned long long>(size+1),zenCount);
    }
    // A stale loose-file identity disables the ZEN index just like stale archives.
    auto zenIndex=json(base/"zen/zen.json");
    require(zenIndex["loose_files"].Size()>0,"installation must include loose ZEN presets");
    auto& loose=zenIndex["loose_files"][0];
    const auto size=loose["size"].GetUint64();
    loose["size"].SetUint64(size+1); write(base/"zen/zen.json",zenIndex);
    const auto census=root/"loose-mismatch.json";
    setenv("OPENGOTHIC_CENSUS",census.c_str(),1);
    bool rejected=false;
    try { Resources resources; Gothic gothic; }
    catch(const BaseStore::Error&) { rejected=true; }
    require(rejected,"loose size +1 must reject the base");
    std::printf("Selection: loose file %s size %llu -> %llu rejected\n",loose["path"].GetString(),
                static_cast<unsigned long long>(size),static_cast<unsigned long long>(size+1));
    }
  };

int main() {
  const auto root=fs::current_path()/("worlddata-contract-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  int result=0;
  try {
    const auto* gothic=std::getenv("GOTHIC_DIR");
    WorldDataContract::require(gothic && *gothic,"GOTHIC_DIR must point to a Gothic II installation");
    const char* args[]={"gothic-worlddata-contract","-g",gothic,"-g2"};
    CommandLine commandLine(4,args);
    fs::create_directories(root);
    WorldDataContract::run(root,gothic);
    std::puts("World data contract passed");
    }
  catch(const std::exception& error) {
    std::fprintf(stderr,"World data contract: %s\n",error.what()); result=1;
    }
  std::error_code error; fs::remove_all(root,error);
  return result;
  }
