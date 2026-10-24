// Converts Gothic resources into our own formats (docs/asset-migration.md).
//
//   content-convert world <gothic dir> <WORLD.ZEN> <out dir>
//       Cut the world mesh into chunks/<x>_<z>.glb + world.json, read them
//       back and compare with the source mesh bit for bit.
//   content-convert verify-world <gothic dir> <WORLD.ZEN> <world dir> [override dir]
//       Compare existing chunks with the source mesh.
//   content-convert textures <gothic dir> <out dir>
//       Every compiled texture (*-C.TEX) as <NAME>.dds (DXT blocks and mips
//       copied as the engine builds them) or <NAME>.png (other formats, top
//       level, as the engine uploads them); raw .TGA files copied as they are;
//       textures.json lists them with the archives they came from.
//   content-convert verify-textures <gothic dir> <dir>
//   content-convert meshes|verify-meshes <gothic dir> <dir>
//       Every static mesh (*.MRM) as <NAME>.glb (common/basemesh.h);
//       meshes.json lists them with the source archives.
//   content-convert --self-test
//       Round trip of a synthetic mesh; no game files.
//
// The output is Gothic content: it never goes into git.

#include "basemesh.h"
#include "worldchunks.h"

#include <zenkit/Logger.hh>
#include <zenkit/MultiResolutionMesh.hh>
#include <zenkit/Stream.hh>
#include <zenkit/Vfs.hh>
#include <zenkit/Texture.hh>
#include <zenkit/World.hh>
#include <zenkit/addon/texcvt.hh>

// miniz otherwise #defines crc32 and other zlib names.
#define MINIZ_NO_ZLIB_COMPATIBLE_NAMES
#include "miniz.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <fstream>
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

struct Archive {
  std::string name; // upper case file name
  uint64_t    size;
  };

// Every Data/*.vdf with OLDER, as the engine mounts them (resources.cpp):
// for each name the newest archive's copy wins, whatever the mount order.
std::vector<Archive> mountArchives(zenkit::Vfs& vfs, const fs::path& gothic) {
  std::map<std::string,fs::path> archives;
  for(const auto& e:fs::directory_iterator(gothic/"Data"))
    if(e.is_regular_file() && upper(e.path().extension().string())==".VDF")
      archives[upper(e.path().filename().string())] = e.path();
  require(!archives.empty(),"no archives in "+(gothic/"Data").string());
  std::vector<Archive> mounted;
  for(const auto& [name,path]:archives) {
    vfs.mount_disk(path,zenkit::VfsOverwriteBehavior::OLDER);
    mounted.push_back({name,uint64_t(fs::file_size(path))});
    }
  return mounted;
  }

std::vector<std::byte> readAll(const zenkit::VfsNode& file) {
  std::vector<std::byte> bytes;
  auto in = file.open_read();
  std::byte buf[1<<16];
  for(;;) {
    const auto n = in->read(buf,sizeof(buf));
    if(n==0)
      break;
    bytes.insert(bytes.end(),buf,buf+n);
    }
  return bytes;
  }

// The engine checks size and CRC32 of the .ZEN it opened against world.json.
SourceWorld loadWorld(const fs::path& gothic, const std::string& name) {
  zenkit::Vfs vfs;
  mountArchives(vfs,gothic);
  const auto* file = vfs.find(name);
  require(file!=nullptr,"missing world "+name);

  const auto bytes = readAll(*file);
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

bool isDxt(zenkit::TextureFormat f) {
  return f==zenkit::TextureFormat::DXT1 || f==zenkit::TextureFormat::DXT2 || f==zenkit::TextureFormat::DXT3 ||
         f==zenkit::TextureFormat::DXT4 || f==zenkit::TextureFormat::DXT5;
  }

void collectFiles(const zenkit::VfsNode& node, std::vector<const zenkit::VfsNode*>& out) {
  for(const auto& child:node.children()) {
    if(child.type()==zenkit::VfsNodeType::DIRECTORY)
      collectFiles(child,out);
    else
      out.push_back(&child);
    }
  }

bool endsWith(const std::string& s, const std::string& tail) {
  return s.size()>=tail.size() && s.compare(s.size()-tail.size(),tail.size(),tail)==0;
  }

std::vector<std::byte> readHost(const fs::path& path) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);
  require(bool(f),"cannot open "+path.string());
  std::vector<std::byte> b(static_cast<size_t>(f.tellg()));
  f.seekg(0);
  f.read(reinterpret_cast<char*>(b.data()),std::streamsize(b.size()));
  require(bool(f),"cannot read "+path.string());
  return b;
  }

void writeHost(const fs::path& path, const void* data, size_t size) {
  const auto tmp = path.string()+".tmp";
  {
  std::ofstream f(tmp,std::ios::binary|std::ios::trunc);
  f.write(static_cast<const char*>(data),std::streamsize(size));
  require(bool(f),"cannot write "+tmp);
  }
  fs::rename(tmp,path);
  }

std::vector<uint8_t> encodePng(const std::vector<uint8_t>& rgba, uint32_t w, uint32_t h) {
  size_t len = 0;
  void* png = tdefl_write_image_to_png_file_in_memory_ex(rgba.data(),int(w),int(h),4,&len,MZ_DEFAULT_LEVEL,MZ_FALSE);
  require(png!=nullptr,"PNG encoding failed");
  std::vector<uint8_t> out(static_cast<uint8_t*>(png),static_cast<uint8_t*>(png)+len);
  mz_free(png);
  return out;
  }

// Decodes the PNGs encodePng writes (8-bit RGBA, filter 0) to check the round trip.
std::vector<uint8_t> decodePng(const std::vector<std::byte>& png, uint32_t& w, uint32_t& h) {
  auto u32 = [&](size_t o) {
    return (uint32_t(png[o])<<24)|(uint32_t(png[o+1])<<16)|(uint32_t(png[o+2])<<8)|uint32_t(png[o+3]);
    };
  require(png.size()>8+25,"PNG too short");
  std::vector<uint8_t> idat;
  size_t o = 8;
  while(o+12<=png.size()) {
    const uint32_t len = u32(o);
    const std::string type(reinterpret_cast<const char*>(png.data()+o+4),4);
    require(o+12+len<=png.size(),"PNG chunk outside the file");
    if(type=="IHDR") {
      w = u32(o+8);
      h = u32(o+12);
      require(uint8_t(png[o+16])==8 && uint8_t(png[o+17])==6,"PNG must be 8-bit RGBA");
      } else if(type=="IDAT") {
      idat.insert(idat.end(),reinterpret_cast<const uint8_t*>(png.data()+o+8),reinterpret_cast<const uint8_t*>(png.data()+o+8+len));
      }
    o += 12+len;
    }
  size_t outLen = 0;
  void* raw = tinfl_decompress_mem_to_heap(idat.data(),idat.size(),&outLen,TINFL_FLAG_PARSE_ZLIB_HEADER);
  require(raw!=nullptr,"PNG inflate failed");
  const auto* r = static_cast<const uint8_t*>(raw);
  const size_t stride = size_t(w)*4;
  std::vector<uint8_t> rgba;
  if(outLen==(stride+1)*h) {
    rgba.reserve(stride*h);
    for(uint32_t y=0; y<h; ++y) {
      if(r[y*(stride+1)]!=0) {
        mz_free(raw);
        throw std::runtime_error("PNG row filter is not 0");
        }
      rgba.insert(rgba.end(),r+y*(stride+1)+1,r+(y+1)*(stride+1));
      }
    }
  mz_free(raw);
  require(rgba.size()==stride*h,"PNG size does not match IHDR");
  return rgba;
  }

std::string jsonName(const std::string& s) {
  for(const char c:s)
    require(static_cast<unsigned char>(c)>=0x20 && static_cast<unsigned char>(c)<0x80 && c!='"' && c!='\\',
            "texture name outside plain ASCII: "+s);
  return "\""+s+"\"";
  }

std::string archivesJson(const std::vector<Archive>& archives) {
  std::string m = "[";
  for(size_t i=0; i<archives.size(); ++i)
    m += std::string(i==0 ? "\n    " : ",\n    ") + "{\"name\": " + jsonName(archives[i].name) +
         ", \"size\": " + std::to_string(archives[i].size) + "}";
  return m + "\n  ]";
  }

// Writes (or, with verifyOnly, re-reads and compares) every texture.
int convertTextures(const fs::path& gothic, const fs::path& out, bool verifyOnly) {
  const auto t0 = std::chrono::steady_clock::now();
  zenkit::Vfs vfs;
  const auto archives = mountArchives(vfs,gothic);
  std::vector<const zenkit::VfsNode*> files;
  collectFiles(vfs.root(),files);
  std::map<std::string,const zenkit::VfsNode*> compiled, raw;
  for(const auto* f:files) {
    const auto name = upper(f->name());
    if(endsWith(name,"-C.TEX"))
      compiled[name.substr(0,name.size()-6)] = f;
    else if(endsWith(name,".TGA"))
      raw[name.substr(0,name.size()-4)] = f;
    }
  if(!verifyOnly)
    fs::create_directories(out);

  std::map<std::string,std::string> index;
  size_t dds = 0, png = 0, tga = 0, bad = 0;
  uint64_t bytes = 0;
  for(const auto& [stem,node]:compiled) {
    try {
      zenkit::Texture tex;
      auto in = node->open_read();
      tex.load(in.get());
      if(isDxt(tex.format())) {
        const auto data = zenkit::to_dds(tex);
        const auto file = stem+".dds";
        if(verifyOnly)
          require(readHost(out/file)==data,"differs: "+file);
        else
          writeHost(out/file,data.data(),data.size());
        index[stem] = file;
        bytes += data.size();
        ++dds;
        } else {
        const auto rgba = tex.as_rgba8(0);
        const auto file = stem+".png";
        if(!verifyOnly) {
          const auto data = encodePng(rgba,tex.width(),tex.height());
          writeHost(out/file,data.data(),data.size());
          }
        uint32_t w = 0, h = 0;
        const auto back = decodePng(readHost(out/file),w,h);
        require(w==tex.width() && h==tex.height() && back==rgba,"differs: "+file);
        index[stem] = file;
        bytes += fs::file_size(out/file);
        ++png;
        }
      }
    catch(const std::exception& e) {
      std::cerr << stem << "-C.TEX: " << e.what() << "\n";
      ++bad;
      }
    }
  for(const auto& [stem,node]:raw) {
    if(compiled.count(stem))
      continue; // the engine reads the compiled texture first
    try {
      const auto data = readAll(*node);
      const auto file = stem+".tga";
      if(verifyOnly)
        require(readHost(out/file)==data,"differs: "+file);
      else
        writeHost(out/file,data.data(),data.size());
      index[stem] = file;
      bytes += data.size();
      ++tga;
      }
    catch(const std::exception& e) {
      std::cerr << stem << ".TGA: " << e.what() << "\n";
      ++bad;
      }
    }

  if(!verifyOnly) {
    std::string m = "{\n  \"format\": \"gothic-textures\",\n  \"version\": 1,\n  \"archives\": " + archivesJson(archives) +
                    ",\n  \"textures\": {";
    bool first = true;
    for(const auto& [stem,file]:index) {
      m += std::string(first ? "\n    " : ",\n    ") + jsonName(stem) + ": " + jsonName(file);
      first = false;
      }
    m += "\n  }\n}\n";
    writeHost(out/"textures.json",m.data(),m.size());
    }
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
  std::cout << (verifyOnly ? "verified " : "written ") << index.size() << " textures: " << dds << " dds, " << png
            << " png, " << tga << " tga; " << (bytes>>20) << " MiB; " << bad << " failed (" << ms << " ms)\n";
  return bad==0 ? 0 : 1;
  }

int convertMeshes(const fs::path& gothic, const fs::path& out, bool verifyOnly) {
  const auto t0 = std::chrono::steady_clock::now();
  zenkit::Vfs vfs;
  const auto archives = mountArchives(vfs,gothic);
  std::vector<const zenkit::VfsNode*> files;
  collectFiles(vfs.root(),files);
  std::map<std::string,const zenkit::VfsNode*> meshes;
  for(const auto* f:files) {
    const auto name = upper(f->name());
    if(endsWith(name,".MRM"))
      meshes[name.substr(0,name.size()-4)] = f;
    }
  if(!verifyOnly)
    fs::create_directories(out);
  std::map<std::string,std::string> index;
  size_t bad = 0, triangles = 0;
  uint64_t bytes = 0;
  for(const auto& [stem,node]:meshes) {
    try {
      zenkit::MultiResolutionMesh mrm;
      auto in = node->open_read();
      mrm.load(in.get());
      const auto file = stem+".glb";
      if(!verifyOnly)
        writeBaseMesh(mrm,stem,out/file);
      const auto diff = compareBaseMeshes(mrm,readBaseMesh(out/file));
      require(diff.empty(),diff);
      for(const auto& sm:mrm.sub_meshes)
        triangles += sm.triangles.size();
      index[stem] = file;
      bytes += fs::file_size(out/file);
      }
    catch(const std::exception& e) {
      std::cerr << stem << ".MRM: " << e.what() << "\n";
      ++bad;
      }
    }
  if(!verifyOnly) {
    std::string m = "{\n  \"format\": \"gothic-meshes\",\n  \"version\": 1,\n  \"archives\": " + archivesJson(archives) +
                    ",\n  \"meshes\": {";
    bool first = true;
    for(const auto& [stem,file]:index) {
      m += std::string(first ? "\n    " : ",\n    ") + jsonName(stem) + ": " + jsonName(file);
      first = false;
      }
    m += "\n  }\n}\n";
    writeHost(out/"meshes.json",m.data(),m.size());
    }
  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
  std::cout << (verifyOnly ? "verified " : "written ") << index.size() << " meshes, " << triangles << " triangles; "
            << (bytes>>20) << " MiB; " << bad << " failed (" << ms << " ms)\n";
  return bad==0 ? 0 : 1;
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

  // A static mesh: an unused position, an empty submesh, a sector-like name.
  zenkit::MultiResolutionMesh mrm;
  mrm.positions = {{0,0,0},{100.5f,0,0},{0,-20.25f,3},{7,7,7}};
  mrm.alpha_test = 1;
  mrm.bbox.min = {0,-20.25f,0};
  mrm.bbox.max = {100.5f,0,3};
  mrm.sub_meshes.resize(2);
  mrm.sub_meshes[0].mat = material("EMPTY","EMPTY.TGA",zenkit::MaterialGroup::WOOD);
  mrm.sub_meshes[1].mat = material("BARREL_\xE4","BARREL.TGA",zenkit::MaterialGroup::WOOD);
  mrm.sub_meshes[1].wedges = {{{0,1,0},{0,0},0},{{0,1,0},{1,0},1},{{0.6f,0.8f,-0.0f},{0.5f,1},2},{{0,1,0},{0,0},2}};
  mrm.sub_meshes[1].triangles = {{{0,1,2}},{{2,1,3}}};
  writeBaseMesh(mrm,"TEST_MESH",dir/"mesh.glb");
  diff = compareBaseMeshes(mrm,readBaseMesh(dir/"mesh.glb"));
  require(diff.empty(),"mesh round trip: "+diff);

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
    if(args.size()==3 && (args[0]=="meshes" || args[0]=="verify-meshes"))
      return convertMeshes(args[1],args[2],args[0]=="verify-meshes");
    if(args.size()==3 && (args[0]=="textures" || args[0]=="verify-textures"))
      return convertTextures(args[1],args[2],args[0]=="verify-textures");
    if((args.size()==4 || args.size()==5) && args[0]=="verify-world") {
      const auto world = loadWorld(args[1],args[2]);
      return verify(world.mesh,args[3],args.size()==5 ? fs::path(args[4]) : fs::path());
      }
    std::cerr << "usage: content-convert world|verify-world <gothic dir> <WORLD.ZEN> <dir> [override]\n"
                 "       content-convert textures|verify-textures|meshes|verify-meshes <gothic dir> <dir>\n"
                 "       content-convert --self-test\n";
    return 2;
    }
  catch(const std::exception& e) {
    std::cerr << "content-convert: " << e.what() << "\n";
    return 1;
    }
  }
