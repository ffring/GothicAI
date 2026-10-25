#include "baseimport.h"
#include "baseformat.h"
#include "asset/fromzenkit.h"
#include "basemesh.h"
#include "worldchunks.h"
#include "worlddata.h"
#include "gothicglb.h"

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
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/evp.h>
#endif

namespace fs = std::filesystem;

namespace BaseImport {
namespace {

thread_local std::string* importError=nullptr;

struct ImportErrors {
  std::string error;
  std::string* previous=std::exchange(importError,&error);
  ~ImportErrors() { importError=previous; }
  void check() const {
    if(!error.empty()) throw std::runtime_error("ZenKit "+error);
    }
  };

void require(bool ok, const std::string& what) {
  if(!ok)
    throw std::runtime_error(what);
  }

std::string upper(std::string s) {
  for(auto& c:s)
    c = char(std::toupper(static_cast<unsigned char>(c)));
  return s;
  }

struct Archive {
  std::string name; // upper case file name
  uint64_t    size;
  };

struct SourceWorld {
  WorldChunkSource source;
  asset::WorldMesh mesh;
  WorldData::Data  data;
  std::vector<Archive> archives;
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
  const auto archives = mountArchives(vfs,gothic);
  const auto* file = vfs.find(name);
  require(file!=nullptr,"missing world "+name);

  const auto bytes = readAll(*file);
  SourceWorld r;
  r.source.world = upper(name);
  r.source.size  = bytes.size();
  r.source.crc32 = worldChunkCrc32(bytes.data(),bytes.size());

  zenkit::World world;
  auto in = zenkit::Read::from(&bytes);
  try {
    ImportErrors errors;
    world.load(in.get(),zenkit::GameVersion::GOTHIC_2);
    errors.check();
    }
  catch(const std::exception& e) { throw std::runtime_error(name+": "+e.what()); }
  r.data = WorldData::fromWorld(world,r.source);
  r.archives = archives;
  r.mesh = asset::fromZenKit(world.world_mesh);
  return r;
  }

int verify(const asset::WorldMesh& source, const fs::path& dir, const fs::path& overrideDir) {
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

void addWorldArchives(const fs::path& dir, const std::vector<Archive>& archives) {
  const auto bytes=readHost(dir/"world.json");
  std::string text(reinterpret_cast<const char*>(bytes.data()),bytes.size());
  require(!text.empty() && text[0]=='{',"invalid world.json");
  text.insert(1,"\n  \"archives\": "+archivesJson(archives)+",");
  writeHost(dir/"world.json",text.data(),text.size());
  }

rapidjson::Document parseJson(const fs::path& file) {
  const auto bytes=readHost(file);
  rapidjson::Document doc;
  doc.Parse<rapidjson::kParseFullPrecisionFlag>(reinterpret_cast<const char*>(bytes.data()),bytes.size());
  require(!doc.HasParseError() && doc.IsObject(),"invalid JSON: "+file.string());
  return doc;
  }

int verifyWorld(const SourceWorld& world, const fs::path& dir, const fs::path& overrideDir) {
  const auto t0=std::chrono::steady_clock::now();
  if(verify(world.mesh,dir,overrideDir)!=0) return 1;
  const auto manifest=readWorldChunkManifest(dir);
  require(manifest.source.world==world.source.world && manifest.source.size==world.source.size &&
          manifest.source.crc32==world.source.crc32,"world.json source differs");
  const auto json=parseJson(dir/"world.json");
  rapidjson::Document archives;
  archives.Parse(archivesJson(world.archives).c_str());
  require(json.HasMember("archives") && json["archives"]==archives,"world.json archives differ");
  const auto data=WorldData::read(dir);
  const auto diff=WorldData::compare(world.data,data);
  require(diff.empty(),world.source.world+": "+diff);
  uint64_t bytes=0;
  for(const auto* file:{"vobs.json","waynet.json","sectors.json","bsp.bin","rooms.bin"}) bytes+=fs::file_size(dir/file);
  const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
  std::cout << world.source.world << ": vobs " << WorldData::vobCount(data.vobs)
            << ", points " << (data.waynet ? data.waynet->points.size() : 0)
            << ", edges " << (data.waynet ? data.waynet->edges.size() : 0)
            << ", sectors " << data.bsp.sectors.size() << ", nodes " << data.bsp.nodes.size()
            << ", leaves " << data.bsp.leaf_node_indices.size() << "; data " << bytes
            << " bytes; ZenKit ASCII, BSP and rooms equal (" << ms << " ms)\n";
  return 0;
  }

}

void onZenkitLog(zenkit::LogLevel level, const char* component, const char* message) {
  if(level==zenkit::LogLevel::ERROR && importError!=nullptr && importError->empty())
    *importError=std::string(component)+": "+message;
  }

int convertZen(const fs::path& gothic, const fs::path& out, bool verifyOnly) {
  const auto t0=std::chrono::steady_clock::now();
  std::string openedSource;
  zenkit::Vfs vfs;
  vfs.set_open_observer([&](std::string_view, std::string_view source) { openedSource=source; });
  const auto archives=mountArchives(vfs,gothic);
  // Match Resources::mountWork, including its priority and VFS name lookup.
  for(const auto& entry:fs::directory_iterator(gothic))
    if(entry.is_directory() && upper(entry.path().filename().string())=="_WORK") {
      vfs.mkdir("/_work");
      vfs.mount_host(fs::absolute(entry.path()),"/_work",zenkit::VfsOverwriteBehavior::NONE);
      break;
      }
  std::vector<const zenkit::VfsNode*> files;
  collectFiles(vfs.root(),files);
  std::map<std::string,const zenkit::VfsNode*> zens;
  for(const auto* file:files)
    if(endsWith(upper(file->name()),".ZEN")) zens[upper(file->name())]=vfs.find(file->name());
  if(!verifyOnly) fs::create_directories(out);
  std::string entries="{", loose="[";
  uint64_t bytes=0;
  size_t count=0, worlds=0, looseCount=0;
  for(const auto& [name,node]:zens) {
    if(name=="LENSFLARE.ZEN") {
      std::cout << "skipped LENSFLARE.ZEN: not consumed by the engine\n";
      continue;
      }
    const auto sourceBytes=readAll(*node);
    WorldData::ZenFile zen;
    const WorldChunkSource source{name,sourceBytes.size(),worldChunkCrc32(sourceBytes.data(),sourceBytes.size())};
    try {
      ImportErrors errors;
      const bool meshless=WorldData::loadZen(sourceBytes,source,zen);
      errors.check();
      if(!meshless) { ++worlds; continue; }
      const auto file=name.substr(0,name.size()-4)+".json";
      if(!verifyOnly) WorldData::writeZen(zen,out/file);
      const auto diff=WorldData::compareZen(zen,WorldData::readZen(out/file));
      require(diff.empty(),diff);
      if(count++) entries+=",";
      entries+="\n    "+gothicglb::jsonString(name)+":"+gothicglb::jsonString(file);
      if(fs::path(openedSource).is_absolute()) {
        const auto path=fs::path(openedSource).lexically_relative(fs::absolute(gothic)).generic_string();
        if(looseCount++) loose+=",";
        loose+="\n    {\"path\":"+gothicglb::jsonString(path)+",\"size\":"+std::to_string(sourceBytes.size())+"}";
        }
      bytes+=fs::file_size(out/file);
      }
    catch(const std::exception& e) { throw std::runtime_error(name+": "+e.what()); }
    }
  const auto index="{\n  \"format\":1,\n  \"archives\":"+archivesJson(archives)+
                   ",\n  \"loose_files\":"+loose+"\n  ],\n  \"files\":"+entries+"\n  }\n}\n";
  if(!verifyOnly) writeHost(out/"zen.json",index.data(),index.size());
  const auto actual=parseJson(out/"zen.json");
  rapidjson::Document expected; expected.Parse(index.c_str());
  require(actual==expected,"zen.json files, archives or loose files differ");
  const auto ms=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
  std::cout << (verifyOnly ? "verified " : "written and verified ") << count << " ZEN files, " << worlds
            << " mesh worlds excluded; " << bytes << " bytes; 0 failed (" << ms << " ms)\n";
  return 0;
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
      ImportErrors errors;
      zenkit::Texture tex;
      auto in = node->open_read();
      tex.load(in.get());
      errors.check();
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
      ImportErrors errors;
      zenkit::MultiResolutionMesh archiveMesh;
      auto in = node->open_read();
      archiveMesh.load(in.get());
      errors.check();
      const auto mrm = asset::fromZenKit(archiveMesh);
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


int convertWorld(const fs::path& gothic, const std::string& name, const fs::path& out) {
      const auto world = loadWorld(gothic,name);
      std::cout << world.source.world << ": " << world.source.size << " bytes, crc32 " << world.source.crc32 << "\n";
      const auto t0 = std::chrono::steady_clock::now();
      const auto manifest = writeWorldChunks(world.mesh,world.source,out);
      WorldData::write(world.data,out);
      addWorldArchives(out,world.archives);
      const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-t0).count();
      std::cout << manifest.chunks.size() << " chunks written to " << out.string() << " (" << ms << " ms); unreferenced: "
                << manifest.unreferencedVertices << " vertices, " << manifest.unreferencedFeatures << " features\n";
      return verifyWorld(world,out,{});
  }

int verifyWorld(const fs::path& gothic, const std::string& name, const fs::path& out, const fs::path& overrides) {
  return verifyWorld(loadWorld(gothic,name),out,overrides);
  }

std::string sha256(std::string_view bytes) {
  unsigned char digest[32];
#ifdef __APPLE__
  require(bytes.size()<=UINT32_MAX,"SHA-256 input too large");
  CC_SHA256(bytes.data(),CC_LONG(bytes.size()),digest);
#else
  unsigned int size=0;
  require(EVP_Digest(bytes.data(),bytes.size(),digest,&size,EVP_sha256(),nullptr)==1 && size==32,
          "SHA-256 failed");
#endif
  std::string hex;
  for(auto byte:digest) {
    hex.push_back("0123456789abcdef"[byte>>4]);
    hex.push_back("0123456789abcdef"[byte&15]);
    }
  return hex;
  }

Identity identify(const fs::path& gothic, Files mounted) {
  std::sort(mounted.begin(),mounted.end());
  Files loose;
  for(const auto& dir:fs::directory_iterator(gothic))
    if(dir.is_directory() && upper(dir.path().filename().string())=="_WORK") {
      for(const auto& file:fs::recursive_directory_iterator(dir.path()))
        if(file.is_regular_file() && upper(file.path().extension().string())==".ZEN")
          loose.emplace_back(file.path().lexically_relative(gothic).generic_string(),file.file_size());
      break;
      }
  std::sort(loose.begin(),loose.end());
  const auto entries=[](const Files& files, const char* key) {
    std::string json="[";
    for(const auto& [name,size]:files) {
      if(json.size()>1) json+=",";
      json+="\n    {\""+std::string(key)+"\":"+gothicglb::jsonString(name)+",\"size\":"+std::to_string(size)+"}";
      }
    return json+"\n  ]";
    };
  const auto source="\n  \"format_sha256\":\""+std::string(BaseFormatSha256)+"\",\n  \"archives\":"+
                    entries(mounted,"name")+",\n  \"loose_files\":"+entries(loose,"path")+"\n}\n";
  Identity identity;
  identity.fingerprint=sha256("{"+source);
  identity.json="{\n  \"fingerprint\":\""+identity.fingerprint+"\","+source;
  return identity;
  }

Identity identify(const fs::path& gothic) {
  zenkit::Vfs vfs;
  Files mounted;
  for(const auto& archive:mountArchives(vfs,gothic))
    mounted.emplace_back(archive.name,archive.size);
  return identify(gothic,std::move(mounted));
  }

bool matches(const fs::path& dir, const Identity& identity) {
  try {
    const auto actual=parseJson(dir/"base.json");
    rapidjson::Document expected;
    expected.Parse(identity.json.c_str());
    return actual==expected;
    }
  catch(const std::exception&) { return false; }
  }

void build(const fs::path& gothic, const fs::path& out, const Progress& progress) {
  const auto identity=identify(gothic);
  fs::remove(out/"base.json");
  for(const char* world:{"NEWWORLD","OLDWORLD","ADDONWORLD","DRAGONISLAND"}) {
    fs::remove_all(out/"worlds"/world);
    if(progress) progress(std::string("base import: ")+world);
    require(convertWorld(gothic,std::string(world)+".ZEN",out/"worlds"/world)==0,"world import failed");
    }
  for(const char* kind:{"zen","textures","meshes"}) fs::remove_all(out/kind);
  if(progress) progress("base import: ZEN files");
  require(convertZen(gothic,out/"zen",false)==0,"ZEN import failed");
  if(progress) progress("base import: textures");
  require(convertTextures(gothic,out/"textures",false)==0,"texture import failed");
  if(progress) progress("base import: meshes");
  require(convertMeshes(gothic,out/"meshes",false)==0,"mesh import failed");
  require(identify(gothic).json==identity.json,"installation changed during base import");
  writeHost(out/"base.json",identity.json.data(),identity.json.size());
  }

}
