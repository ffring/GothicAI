// Converts Gothic resources into our own formats (docs/asset-migration.md).
//
//   content-convert world <gothic dir> <WORLD.ZEN> <out dir>
//       Cut the world mesh into chunks/<x>_<z>.glb + world.json, read them
//       back and compare with the source mesh bit for bit.
//   content-convert verify-world <gothic dir> <WORLD.ZEN> <world dir> [override dir]
//       Compare existing chunks with the source mesh.
//   content-convert --self-test
//       Round trip of a synthetic mesh; no game files.
//
// The output is Gothic content: it never goes into git.

#include "worldchunks.h"

#include <zenkit/Logger.hh>
#include <zenkit/Stream.hh>
#include <zenkit/Vfs.hh>
#include <zenkit/World.hh>

#include <cctype>
#include <chrono>
#include <cstddef>
#include <filesystem>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

void require(bool ok, const std::string& what) {
  if(!ok)
    throw std::runtime_error(what);
  }

std::string upper(std::string s) {
  for(auto& c:s)
    c = char(std::toupper(static_cast<unsigned char>(c)));
  return s;
  }

struct SourceWorld {
  WorldChunkSource source;
  zenkit::Mesh     mesh;
  };

// Worlds.vdf, then Worlds_Addon.vdf on top: the addon's copy of a world wins.
// The engine checks size and CRC32 of the .ZEN it opened against world.json.
SourceWorld loadWorld(const fs::path& gothic, const std::string& name) {
  std::map<std::string,fs::path> archives;
  for(const auto& e:fs::directory_iterator(gothic/"Data"))
    if(e.is_regular_file())
      archives[upper(e.path().filename().string())] = e.path();
  zenkit::Vfs vfs;
  for(const auto* archive:{"WORLDS.VDF","WORLDS_ADDON.VDF"}) {
    const auto found = archives.find(archive);
    require(found!=archives.end(),std::string("missing archive ")+archive);
    vfs.mount_disk(found->second,zenkit::VfsOverwriteBehavior::ALL);
    }
  const auto* file = vfs.find(name);
  require(file!=nullptr,"missing world "+name);

  std::vector<std::byte> bytes;
  {
  auto in = file->open_read();
  std::byte buf[1<<16];
  for(;;) {
    const auto n = in->read(buf,sizeof(buf));
    if(n==0)
      break;
    bytes.insert(bytes.end(),buf,buf+n);
    }
  }
  SourceWorld r;
  r.source.world = upper(name);
  r.source.size  = bytes.size();
  r.source.crc32 = worldChunkCrc32(bytes.data(),bytes.size());

  zenkit::World world;
  auto in = zenkit::Read::from(&bytes);
  world.load(in.get(),zenkit::GameVersion::GOTHIC_2);
  r.mesh = std::move(world.world_mesh);
  return r;
  }

int verify(const zenkit::Mesh& source, const fs::path& dir, const fs::path& overrideDir) {
  const auto t0 = std::chrono::steady_clock::now();
  const auto expected = canonicalWorldMesh(source);
  const auto actual   = readWorldChunks(dir,overrideDir);
  const auto diff     = compareWorldMeshes(expected,actual);
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
  std::cout << "triangles " << actual.polygons.material_indices.size()
            << ", vertices " << actual.vertices.size()
            << ", features " << actual.features.size()
            << ", materials " << actual.materials.size()
            << " (read + compare " << ms << " ms)\n";
  if(!diff.empty()) {
    std::cout << "MISMATCH: " << diff << "\n";
    return 1;
    }
  std::cout << "equal to the source mesh\n";
  return 0;
  }

zenkit::Material material(const std::string& name, const std::string& texture, zenkit::MaterialGroup group) {
  zenkit::Material m;
  m.name    = name;
  m.texture = texture;
  m.group   = group;
  m.color   = {200,180,160,255};
  m.texture_scale = {0.25f,0.125f};
  m.alpha_func = zenkit::AlphaFunction::NONE;
  return m;
  }

int selfTest() {
  zenkit::Mesh mesh;
  mesh.materials.push_back(material("UNUSED","UNUSED.TGA",zenkit::MaterialGroup::STONE));
  // Sector name with a byte above 0x7F: must survive byte for byte.
  mesh.materials.push_back(material("C:HAFEN_\xE4","NW_HARBOUR_STAIRS_01.TGA",zenkit::MaterialGroup::STONE));
  auto water = material("WATER","NW_WATER.TGA",zenkit::MaterialGroup::WATER);
  water.texture_anim_fps = 0.1f;
  water.wave_mode = zenkit::WaveMode::GROUND;
  water.wave_max_amplitude = 33.3f;
  water.disable_collision = true;
  water.color.a = 128;
  water.alpha_func = zenkit::AlphaFunction::BLEND;
  mesh.materials.push_back(water);

  // A strip crossing three 64 m chunks; vertex 2 and 3 are shared across chunks.
  const float xs[] = {-100.f, 6300.f, 6500.f, 12900.1f, 13000.f, -7000.3f};
  for(unsigned i=0; i<6; ++i) {
    mesh.vertices.push_back({xs[i],float(i)*10.f+0.1f,0.f});
    mesh.vertices.push_back({xs[i],float(i)*10.f,700.7f});
    }
  mesh.vertices.push_back({1.f,2.f,3.f}); // referenced by nothing, as is vertex 9
  for(unsigned i=0; i<14; ++i)
    mesh.features.push_back({{float(i)*0.5f,-float(i)},0xFF000000u|(i*0x010203u),{0.f,1.f,-0.0f}});

  auto& p = mesh.polygons;
  auto tri = [&](uint32_t m, std::array<uint32_t,3> v, std::array<uint32_t,3> f, int32_t lm, int16_t sector) {
    p.material_indices.push_back(m);
    p.lightmap_indices.push_back(lm);
    for(unsigned c=0; c<3; ++c) {
      p.vertex_indices.push_back(v[c]);
      p.feature_indices.push_back(f[c]);
      }
    zenkit::PolygonFlagSet flags {};
    flags.is_sector = 1;
    flags.normal_axis = 2;
    flags.sector_index = sector;
    p.flags.push_back(flags);
    };
  tri(1,{0,1,2},{0,1,2},-1,-1);
  tri(2,{2,1,3},{2,1,3},5,7);
  tri(1,{2,3,4},{4,5,6},-1,-1);   // same vertices, other features
  tri(1,{4,5,6},{6,5,7},12,300);
  tri(2,{6,7,8},{8,9,10},-1,-32768);
  tri(1,{10,11,0},{11,12,13},-1,0);

  const auto dir = fs::temp_directory_path()/("content-convert-self-test-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  WorldChunkSource source {"TEST.ZEN",1234,0xDEADBEEFu};
  const auto manifest = writeWorldChunks(mesh,source,dir);
  require(manifest.chunks.size()==4,"expected 4 chunks, got "+std::to_string(manifest.chunks.size()));
  const auto back = readWorldChunkManifest(dir);
  require(back.source.world=="TEST.ZEN" && back.source.size==1234 && back.source.crc32==0xDEADBEEFu,"manifest source");
  require(back.triangles==6,"manifest triangles");

  const auto expected = canonicalWorldMesh(mesh);
  require(expected.materials.size()==2 && expected.vertices.size()==11,"canonical form drops unused entries");
  auto diff = compareWorldMeshes(expected,readWorldChunks(dir));
  require(diff.empty(),"round trip: "+diff);

  // A chunk in the override directory replaces the base one by name.
  const auto over = dir/"override";
  fs::create_directories(over/"chunks");
  fs::copy_file(dir/manifest.chunks[0],over/manifest.chunks[0]);
  diff = compareWorldMeshes(expected,readWorldChunks(dir,over));
  require(diff.empty(),"override round trip: "+diff);
  fs::remove(over/manifest.chunks[0]);
  fs::copy_file(dir/manifest.chunks[1],over/manifest.chunks[0]);
  bool rejected = false;
  try {
    readWorldChunks(dir,over);
    }
  catch(const std::exception&) {
    rejected = true; // the same source triangles twice
    }
  require(rejected,"duplicate triangle order must be rejected");

  fs::remove_all(dir);
  std::cout << "self-test passed\n";
  return 0;
  }

}

int main(int argc, char** argv) {
  try {
    zenkit::Logger::set(zenkit::LogLevel::ERROR,[](auto,const char* component,const char* message) {
      std::cerr << "ZenKit " << component << ": " << message << '\n';
      });
    const std::vector<std::string> args(argv+1,argv+argc);
    if(args.size()==1 && args[0]=="--self-test")
      return selfTest();
    if(args.size()==4 && args[0]=="world") {
      const auto world = loadWorld(args[1],args[2]);
      std::cout << world.source.world << ": " << world.source.size << " bytes, crc32 " << world.source.crc32 << "\n";
      const auto t0 = std::chrono::steady_clock::now();
      const auto manifest = writeWorldChunks(world.mesh,world.source,args[3]);
      const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
      std::cout << manifest.chunks.size() << " chunks written to " << args[3] << " (" << ms << " ms)\n";
      return verify(world.mesh,args[3],{});
      }
    if((args.size()==4 || args.size()==5) && args[0]=="verify-world") {
      const auto world = loadWorld(args[1],args[2]);
      return verify(world.mesh,args[3],args.size()==5 ? fs::path(args[4]) : fs::path());
      }
    std::cerr << "usage: content-convert world|verify-world <gothic dir> <WORLD.ZEN> <dir> [override] | --self-test\n";
    return 2;
    }
  catch(const std::exception& e) {
    std::cerr << "content-convert: " << e.what() << "\n";
    return 1;
    }
  }
