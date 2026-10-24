#include "worldchunks.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string_view>
#include <tuple>
#include <unordered_map>

#include <rapidjson/document.h>

// The implementation lives in gltfmesh.cpp; this file only uses the API.
#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wconversion"
#endif
#include "../lib/cgltf/cgltf.h"
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

namespace {

constexpr int         formatVersion = 1;
constexpr const char* manifestName  = "world.json";
constexpr const char* chunkFormat   = "gothic-world-chunk";
constexpr const char* worldFormat   = "gothic-world";
// Float attributes carry source indices; floats are exact up to 2^24.
constexpr uint32_t    exactIndexLimit = 1u << 24;
constexpr float       baseScale = 0.01f;

[[noreturn]] void reject(const std::string& reason) {
  throw std::runtime_error("world chunks: " + reason);
  }

uint32_t bits(float v) {
  uint32_t r;
  std::memcpy(&r,&v,sizeof(r));
  return r;
  }

// Gothic strings are single-byte (Windows-125x). JSON needs UTF-8: each byte
// becomes the code point of the same value, and back, so names survive byte for byte.
std::string bytesToUtf8(std::string_view s) {
  std::string out;
  for(const char ch:s) {
    const auto c = static_cast<unsigned char>(ch);
    if(c<0x80) {
      out.push_back(ch);
      } else {
      out.push_back(char(0xC0 | (c>>6)));
      out.push_back(char(0x80 | (c&0x3F)));
      }
    }
  return out;
  }

std::string utf8ToBytes(std::string_view s) {
  std::string out;
  for(size_t i=0; i<s.size(); ++i) {
    const auto c = static_cast<unsigned char>(s[i]);
    if(c<0x80) {
      out.push_back(char(c));
      continue;
      }
    if((c&0xE0)==0xC0 && i+1<s.size()) {
      const unsigned cp = ((c&0x1Fu)<<6) | (static_cast<unsigned char>(s[i+1])&0x3Fu);
      if(cp<0x100) {
        out.push_back(char(cp));
        ++i;
        continue;
        }
      }
    // A name typed in Blender may hold any UTF-8: keep it as is.
    return std::string(s);
    }
  return out;
  }

std::string jsonString(std::string_view s) {
  std::string out = "\"";
  for(const char ch:bytesToUtf8(s)) {
    const auto c = static_cast<unsigned char>(ch);
    if(c=='"' || c=='\\') {
      out.push_back('\\');
      out.push_back(ch);
      } else if(c<0x20) {
      char buf[8];
      std::snprintf(buf,sizeof(buf),"\\u%04x",c);
      out += buf;
      } else {
      out.push_back(ch);
      }
    }
  out.push_back('"');
  return out;
  }

std::string jsonFloat(float v) {
  if(!std::isfinite(v))
    reject("non-finite material value");
  char buf[32];
  // 9 significant digits round-trip every float.
  std::snprintf(buf,sizeof(buf),"%.9g",double(v));
  return buf;
  }

std::string jsonVec2(const zenkit::Vec2& v) {
  return "[" + jsonFloat(v.x) + "," + jsonFloat(v.y) + "]";
  }

std::string materialJson(const zenkit::Material& m, size_t index) {
  std::string s = "{";
  s += "\"index\":" + std::to_string(index);
  s += ",\"name\":" + jsonString(m.name);
  s += ",\"group\":" + std::to_string(int(m.group));
  s += ",\"color\":[" + std::to_string(m.color.r) + "," + std::to_string(m.color.g) + "," +
       std::to_string(m.color.b) + "," + std::to_string(m.color.a) + "]";
  s += ",\"smooth_angle\":" + jsonFloat(m.smooth_angle);
  s += ",\"texture\":" + jsonString(m.texture);
  s += ",\"texture_scale\":" + jsonVec2(m.texture_scale);
  s += ",\"texture_anim_fps\":" + jsonFloat(m.texture_anim_fps);
  s += ",\"texture_anim_map_mode\":" + std::to_string(int(m.texture_anim_map_mode));
  s += ",\"texture_anim_map_dir\":" + jsonVec2(m.texture_anim_map_dir);
  s += ",\"disable_collision\":" + std::string(m.disable_collision ? "true" : "false");
  s += ",\"disable_lightmap\":" + std::string(m.disable_lightmap ? "true" : "false");
  s += ",\"dont_collapse\":" + std::string(m.dont_collapse ? "true" : "false");
  s += ",\"detail_object\":" + jsonString(m.detail_object);
  s += ",\"detail_object_scale\":" + jsonFloat(m.detail_object_scale);
  s += ",\"force_occluder\":" + std::string(m.force_occluder ? "true" : "false");
  s += ",\"environment_mapping\":" + std::string(m.environment_mapping ? "true" : "false");
  s += ",\"environment_mapping_strength\":" + jsonFloat(m.environment_mapping_strength);
  s += ",\"wave_mode\":" + std::to_string(int(m.wave_mode));
  s += ",\"wave_speed\":" + std::to_string(int(m.wave_speed));
  s += ",\"wave_max_amplitude\":" + jsonFloat(m.wave_max_amplitude);
  s += ",\"wave_grid_size\":" + jsonFloat(m.wave_grid_size);
  s += ",\"ignore_sun\":" + std::string(m.ignore_sun ? "true" : "false");
  s += ",\"alpha_func\":" + std::to_string(int(m.alpha_func));
  s += ",\"default_mapping\":" + jsonVec2(m.default_mapping);
  s += "}";
  return s;
  }

using Json = rapidjson::Value;

const Json& member(const Json& obj, const char* name) {
  if(!obj.IsObject() || !obj.HasMember(name))
    reject(std::string("missing field ") + name);
  return obj[name];
  }

float jsonToFloat(const Json& v, const char* name) {
  if(!v.IsNumber())
    reject(std::string(name) + " must be a number");
  return float(v.GetDouble());
  }

int64_t jsonToInt(const Json& v, const char* name) {
  if(!v.IsInt64())
    reject(std::string(name) + " must be an integer");
  return v.GetInt64();
  }

bool jsonToBool(const Json& v, const char* name) {
  if(!v.IsBool())
    reject(std::string(name) + " must be a boolean");
  return v.GetBool();
  }

std::string jsonToString(const Json& v, const char* name) {
  if(!v.IsString())
    reject(std::string(name) + " must be a string");
  return utf8ToBytes(std::string_view(v.GetString(),v.GetStringLength()));
  }

zenkit::Vec2 jsonToVec2(const Json& v, const char* name) {
  if(!v.IsArray() || v.Size()!=2)
    reject(std::string(name) + " must be [x,y]");
  return {jsonToFloat(v[0],name),jsonToFloat(v[1],name)};
  }

template<class E>
E jsonToEnum(const Json& obj, const char* name) {
  return E(jsonToInt(member(obj,name),name));
  }

zenkit::Material materialFromJson(const Json& g) {
  zenkit::Material m;
  m.name  = jsonToString(member(g,"name"),"name");
  m.group = jsonToEnum<zenkit::MaterialGroup>(g,"group");
  const auto& c = member(g,"color");
  if(!c.IsArray() || c.Size()!=4)
    reject("color must be [r,g,b,a]");
  for(unsigned i=0; i<4; ++i) {
    const auto v = jsonToInt(c[i],"color");
    if(v<0 || v>255)
      reject("color component outside 0..255");
    m.color[i] = static_cast<unsigned char>(v);
    }
  m.smooth_angle                 = jsonToFloat(member(g,"smooth_angle"),"smooth_angle");
  m.texture                      = jsonToString(member(g,"texture"),"texture");
  m.texture_scale                = jsonToVec2(member(g,"texture_scale"),"texture_scale");
  m.texture_anim_fps             = jsonToFloat(member(g,"texture_anim_fps"),"texture_anim_fps");
  m.texture_anim_map_mode        = jsonToEnum<zenkit::AnimationMapping>(g,"texture_anim_map_mode");
  m.texture_anim_map_dir         = jsonToVec2(member(g,"texture_anim_map_dir"),"texture_anim_map_dir");
  m.disable_collision            = jsonToBool(member(g,"disable_collision"),"disable_collision");
  m.disable_lightmap             = jsonToBool(member(g,"disable_lightmap"),"disable_lightmap");
  m.dont_collapse                = jsonToBool(member(g,"dont_collapse"),"dont_collapse");
  m.detail_object                = jsonToString(member(g,"detail_object"),"detail_object");
  m.detail_object_scale          = jsonToFloat(member(g,"detail_object_scale"),"detail_object_scale");
  m.force_occluder               = jsonToBool(member(g,"force_occluder"),"force_occluder");
  m.environment_mapping          = jsonToBool(member(g,"environment_mapping"),"environment_mapping");
  m.environment_mapping_strength = jsonToFloat(member(g,"environment_mapping_strength"),"environment_mapping_strength");
  m.wave_mode                    = jsonToEnum<zenkit::WaveMode>(g,"wave_mode");
  m.wave_speed                   = jsonToEnum<zenkit::WaveSpeed>(g,"wave_speed");
  m.wave_max_amplitude           = jsonToFloat(member(g,"wave_max_amplitude"),"wave_max_amplitude");
  m.wave_grid_size               = jsonToFloat(member(g,"wave_grid_size"),"wave_grid_size");
  m.ignore_sun                   = jsonToBool(member(g,"ignore_sun"),"ignore_sun");
  m.alpha_func                   = jsonToEnum<zenkit::AlphaFunction>(g,"alpha_func");
  m.default_mapping              = jsonToVec2(member(g,"default_mapping"),"default_mapping");
  return m;
  }

bool sameMaterial(const zenkit::Material& a, const zenkit::Material& b) {
  auto eq2 = [](const zenkit::Vec2& x, const zenkit::Vec2& y) {
    return bits(x.x)==bits(y.x) && bits(x.y)==bits(y.y);
    };
  return a.name==b.name && a.group==b.group &&
         a.color.r==b.color.r && a.color.g==b.color.g && a.color.b==b.color.b && a.color.a==b.color.a &&
         bits(a.smooth_angle)==bits(b.smooth_angle) && a.texture==b.texture &&
         eq2(a.texture_scale,b.texture_scale) && bits(a.texture_anim_fps)==bits(b.texture_anim_fps) &&
         a.texture_anim_map_mode==b.texture_anim_map_mode && eq2(a.texture_anim_map_dir,b.texture_anim_map_dir) &&
         a.disable_collision==b.disable_collision && a.disable_lightmap==b.disable_lightmap &&
         a.dont_collapse==b.dont_collapse && a.detail_object==b.detail_object &&
         bits(a.detail_object_scale)==bits(b.detail_object_scale) && a.force_occluder==b.force_occluder &&
         a.environment_mapping==b.environment_mapping &&
         bits(a.environment_mapping_strength)==bits(b.environment_mapping_strength) &&
         a.wave_mode==b.wave_mode && a.wave_speed==b.wave_speed &&
         bits(a.wave_max_amplitude)==bits(b.wave_max_amplitude) && bits(a.wave_grid_size)==bits(b.wave_grid_size) &&
         a.ignore_sun==b.ignore_sun && a.alpha_func==b.alpha_func && eq2(a.default_mapping,b.default_mapping);
  }

// Polygon flags packed into two 16-bit words: bit fields, then sector index.
std::array<uint16_t,2> packFlags(const zenkit::PolygonFlagSet& f) {
  const unsigned lo = (unsigned(f.is_portal)&3u) | (unsigned(f.is_occluder)<<2) | (unsigned(f.is_sector)<<3) |
                      (unsigned(f.should_relight)<<4) | (unsigned(f.is_outdoor)<<5) |
                      (unsigned(f.is_ghost_occluder)<<6) | (unsigned(f.is_dynamically_lit)<<7) |
                      (unsigned(f.is_lod)<<8) | ((unsigned(f.normal_axis)&3u)<<9);
  return {uint16_t(lo),uint16_t(int16_t(f.sector_index))};
  }

zenkit::PolygonFlagSet unpackFlags(uint16_t lo, uint16_t hi) {
  zenkit::PolygonFlagSet f {};
  f.is_portal          = uint8_t(lo&3u);
  f.is_occluder        = uint8_t((lo>>2)&1u);
  f.is_sector          = uint8_t((lo>>3)&1u);
  f.should_relight     = uint8_t((lo>>4)&1u);
  f.is_outdoor         = uint8_t((lo>>5)&1u);
  f.is_ghost_occluder  = uint8_t((lo>>6)&1u);
  f.is_dynamically_lit = uint8_t((lo>>7)&1u);
  f.is_lod             = uint8_t((lo>>8)&1u);
  f.normal_axis        = uint8_t((lo>>9)&3u);
  f.sector_index       = int16_t(hi);
  return f;
  }

// Feature light is ARGB in one word; glTF COLOR_0 is RGBA bytes.
std::array<uint8_t,4> lightToRgba(uint32_t light) {
  return {uint8_t(light>>16),uint8_t(light>>8),uint8_t(light),uint8_t(light>>24)};
  }

uint32_t rgbaToLight(const uint8_t* c) {
  return (uint32_t(c[3])<<24) | (uint32_t(c[0])<<16) | (uint32_t(c[1])<<8) | uint32_t(c[2]);
  }

std::string chunkName(int32_t x, int32_t z) {
  return std::to_string(x) + "_" + std::to_string(z);
  }

int32_t chunkCoord(float v, int32_t size) {
  return int32_t(std::floor(double(v)/double(size)));
  }

class GlbBuilder {
  public:
    std::vector<uint8_t> bin;
    std::vector<std::string> views, accessors;

    template<class T>
    size_t view(const std::vector<T>& data, int target) {
      while(bin.size()%4!=0)
        bin.push_back(0);
      const size_t offset = bin.size();
      const size_t size   = data.size()*sizeof(T);
      bin.resize(offset+size);
      if(size>0)
        std::memcpy(bin.data()+offset,data.data(),size);
      std::string v = "{\"buffer\":0,\"byteOffset\":" + std::to_string(offset) + ",\"byteLength\":" + std::to_string(size);
      if(target!=0)
        v += ",\"target\":" + std::to_string(target);
      v += "}";
      views.push_back(v);
      return views.size()-1;
      }

    template<class T>
    size_t accessor(const std::vector<T>& data, int componentType, const char* type, size_t components,
                    bool normalized, int target, const std::string& minMax = {}) {
      const size_t v = view(data,target);
      std::string a = "{\"bufferView\":" + std::to_string(v) + ",\"componentType\":" + std::to_string(componentType) +
                      ",\"count\":" + std::to_string(data.size()/components) + ",\"type\":\"" + type + "\"";
      if(normalized)
        a += ",\"normalized\":true";
      a += minMax;
      a += "}";
      accessors.push_back(a);
      return accessors.size()-1;
      }

    std::vector<uint8_t> finish(const std::string& jsonBody) const {
      std::string json = jsonBody;
      while(json.size()%4!=0)
        json.push_back(' ');
      std::vector<uint8_t> payload = bin;
      while(payload.size()%4!=0)
        payload.push_back(0);
      std::vector<uint8_t> out;
      auto u32 = [&out](size_t v) {
        if(v>std::numeric_limits<uint32_t>::max())
          reject("chunk larger than 4 GiB");
        for(unsigned i=0; i<4; ++i)
          out.push_back(uint8_t(v>>(8*i)));
        };
      const size_t total = 12 + 8 + json.size() + 8 + payload.size();
      u32(0x46546C67); // "glTF"
      u32(2);
      u32(total);
      u32(json.size());
      u32(0x4E4F534A); // "JSON"
      out.insert(out.end(),json.begin(),json.end());
      u32(payload.size());
      u32(0x004E4942); // "BIN\0"
      out.insert(out.end(),payload.begin(),payload.end());
      return out;
      }

    std::string viewsJson() const     { return join(views); }
    std::string accessorsJson() const { return join(accessors); }

  private:
    static std::string join(const std::vector<std::string>& items) {
      std::string s = "[";
      for(size_t i=0; i<items.size(); ++i) {
        if(i>0)
          s += ",";
        s += items[i];
        }
      return s + "]";
      }
  };

void writeFile(const std::filesystem::path& path, const void* data, size_t size) {
  const auto tmp = path.string() + ".tmp";
  {
  std::ofstream f(tmp,std::ios::binary | std::ios::trunc);
  if(!f)
    reject("cannot write " + tmp);
  f.write(static_cast<const char*>(data),std::streamsize(size));
  if(!f)
    reject("cannot write " + tmp);
  }
  std::filesystem::rename(tmp,path);
  }

std::vector<uint8_t> readFile(const std::filesystem::path& path) {
  std::ifstream f(path,std::ios::binary | std::ios::ate);
  if(!f)
    reject("cannot open " + path.string());
  const auto size = f.tellg();
  if(size<0)
    reject("cannot read " + path.string());
  std::vector<uint8_t> bytes(static_cast<size_t>(size));
  f.seekg(0);
  if(!bytes.empty() && !f.read(reinterpret_cast<char*>(bytes.data()),size))
    reject("cannot read " + path.string());
  return bytes;
  }

void checkPolygons(const zenkit::Mesh& mesh) {
  const auto& p = mesh.polygons;
  const size_t n = p.material_indices.size();
  if(p.vertex_indices.size()!=n*3 || p.feature_indices.size()!=n*3 ||
     p.lightmap_indices.size()!=n || p.flags.size()!=n)
    reject("polygon arrays disagree in length");
  for(size_t i=0; i<n; ++i) {
    if(p.material_indices[i]>=mesh.materials.size())
      reject("material index out of range");
    for(unsigned c=0; c<3; ++c) {
      if(p.vertex_indices[i*3+c]>=mesh.vertices.size())
        reject("vertex index out of range");
      if(p.feature_indices[i*3+c]>=mesh.features.size())
        reject("feature index out of range");
      }
    }
  }

struct ParsedGlb {
  std::vector<uint8_t> bytes;
  std::unique_ptr<cgltf_data,decltype(&cgltf_free)> data {nullptr,cgltf_free};
  };

ParsedGlb parseGlb(const std::filesystem::path& path) {
  ParsedGlb r;
  r.bytes = readFile(path);
  cgltf_options options = {};
  options.type = cgltf_file_type_glb;
  cgltf_data* parsed = nullptr;
  if(cgltf_parse(&options,r.bytes.data(),r.bytes.size(),&parsed)!=cgltf_result_success)
    reject("cannot parse " + path.string());
  r.data.reset(parsed);
  auto& d = *r.data;
  if(d.buffers_count!=1 || d.buffers[0].uri!=nullptr || d.bin==nullptr || d.buffers[0].size>d.bin_size)
    reject(path.string() + ": expected one embedded buffer");
  if(d.extensions_required_count!=0)
    reject(path.string() + ": extensionsRequired is not supported");
  for(size_t i=0; i<d.accessors_count; ++i) {
    if(d.accessors[i].is_sparse)
      reject(path.string() + ": sparse accessors are not supported");
    if(d.accessors[i].buffer_view!=nullptr && d.accessors[i].buffer_view->has_meshopt_compression)
      reject(path.string() + ": compressed buffer views are not supported");
    }
  // Never call cgltf's URI loader: bind the BIN chunk only.
  d.buffers[0].data = const_cast<void*>(d.bin);
  if(cgltf_validate(&d)!=cgltf_result_success)
    reject(path.string() + ": glTF validation failed");
  if(d.scene==nullptr)
    reject(path.string() + ": a default scene is required");
  return r;
  }

const cgltf_accessor* findAttribute(const cgltf_primitive& p, cgltf_attribute_type type, const char* name = nullptr) {
  for(size_t i=0; i<p.attributes_count; ++i) {
    const auto& a = p.attributes[i];
    if(a.type!=type || a.index!=0)
      continue;
    if(name!=nullptr && (a.name==nullptr || std::strcmp(a.name,name)!=0))
      continue;
    return a.data;
    }
  return nullptr;
  }

std::unique_ptr<rapidjson::Document> parseExtras(const cgltf_extras& extras) {
  if(extras.data==nullptr)
    return nullptr;
  auto doc = std::make_unique<rapidjson::Document>();
  doc->Parse<rapidjson::kParseFullPrecisionFlag>(extras.data);
  if(doc->HasParseError() || !doc->IsObject())
    reject("invalid extras JSON");
  if(!doc->HasMember("gothic"))
    return nullptr;
  return doc;
  }

const cgltf_accessor* extrasAccessor(const cgltf_data& d, const rapidjson::Document* extras, const char* name,
                                     size_t count) {
  if(extras==nullptr)
    return nullptr;
  const auto& g = (*extras)["gothic"];
  if(!g.IsObject() || !g.HasMember(name))
    return nullptr;
  const auto index = jsonToInt(g[name],name);
  if(index<0 || size_t(index)>=d.accessors_count)
    reject(std::string("extras.gothic.") + name + " accessor out of range");
  const auto* a = &d.accessors[index];
  if(a->count!=count)
    reject(std::string("extras.gothic.") + name + " count does not match the triangles");
  return a;
  }

// Vertex and feature identity across chunks: the source index carried in
// _VERTEX/_FEATURE plus the exact values, so an edit that moves one copy of
// a shared vertex splits it instead of moving the other chunk's copy.
struct VertexKey {
  int64_t  id;
  uint32_t x,y,z;
  bool operator<(const VertexKey& o) const { return std::tie(id,x,y,z)<std::tie(o.id,o.x,o.y,o.z); }
  };

struct FeatureKey {
  int64_t  id;
  uint32_t u,v,light,nx,ny,nz;
  bool operator<(const FeatureKey& o) const {
    return std::tie(id,u,v,light,nx,ny,nz)<std::tie(o.id,o.u,o.v,o.light,o.nx,o.ny,o.nz);
    }
  };

struct Corner {
  VertexKey         vertex;
  zenkit::Vec3      position;
  FeatureKey        feature;
  zenkit::VertexFeature value;
  };

struct Triangle {
  int64_t                 order;     // source triangle index, or -1 for edited geometry
  size_t                  material;  // index into the reader's material list
  std::array<Corner,3>    corners;
  int32_t                 lightmap;
  zenkit::PolygonFlagSet  flags;
  };

struct ReadMaterial {
  int64_t          index; // source material index, or -1
  zenkit::Material material;
  };

class ChunkReader {
  public:
    std::vector<ReadMaterial> materials;
    std::vector<Triangle>     triangles;

    void read(const std::filesystem::path& path) {
      auto glb = parseGlb(path);
      const auto& d = *glb.data;
      std::vector<const cgltf_node*> pending;
      for(size_t i=0; i<d.scene->nodes_count; ++i)
        pending.push_back(d.scene->nodes[i]);
      while(!pending.empty()) {
        const auto* node = pending.back();
        pending.pop_back();
        if(node->mesh!=nullptr)
          readNode(d,*node);
        for(size_t i=0; i<node->children_count; ++i)
          pending.push_back(node->children[i]);
        }
      ++chunk;
      }

  private:
    size_t  chunk = 0;
    int64_t nextLocal = 0;
    std::map<int64_t,size_t>     byIndex;
    std::map<std::string,size_t> byRecord;

    size_t material(const cgltf_material* m) {
      if(m==nullptr)
        reject("primitive without material");
      const auto extras = parseExtras(m->extras);
      if(extras==nullptr)
        reject(std::string("material ") + (m->name ? m->name : "?") + " has no extras.gothic");
      const auto& g = (*extras)["gothic"];
      auto rec = materialFromJson(g);
      if(g.HasMember("index")) {
        const auto index = jsonToInt(g["index"],"index");
        auto found = byIndex.find(index);
        if(found!=byIndex.end()) {
          if(!sameMaterial(materials[found->second].material,rec))
            reject("material index " + std::to_string(index) + " differs between chunks");
          return found->second;
          }
        materials.push_back({index,std::move(rec)});
        byIndex.emplace(index,materials.size()-1);
        return materials.size()-1;
        }
      const auto key = materialJson(rec,0);
      auto found = byRecord.find(key);
      if(found!=byRecord.end())
        return found->second;
      materials.push_back({-1,std::move(rec)});
      byRecord.emplace(key,materials.size()-1);
      return materials.size()-1;
      }

    void readNode(const cgltf_data& d, const cgltf_node& node) {
      float m[16];
      cgltf_node_transform_world(&node,m);
      // The converter writes centimetres under a 0.01 node scale: read them
      // without arithmetic so the round trip is exact. Anything else (an edit
      // exported from Blender) goes through the full transform.
      const bool raw = m[0]==baseScale && m[5]==baseScale && m[10]==baseScale && m[15]==1.f &&
                       m[1]==0 && m[2]==0 && m[3]==0 && m[4]==0 && m[6]==0 && m[7]==0 &&
                       m[8]==0 && m[9]==0 && m[11]==0 && m[12]==0 && m[13]==0 && m[14]==0;
      double cof[9];
      const double a=m[0], b=m[4], c=m[8], dd=m[1], e=m[5], f=m[9], g=m[2], h=m[6], k=m[10];
      cof[0]=e*k-f*h; cof[1]=f*g-dd*k; cof[2]=dd*h-e*g;
      cof[3]=c*h-b*k; cof[4]=a*k-c*g;  cof[5]=b*g-a*h;
      cof[6]=b*f-c*e; cof[7]=c*dd-a*f; cof[8]=a*e-b*dd;
      const double det = a*cof[0]+b*cof[1]+c*cof[2];
      if(!std::isfinite(det) || det==0)
        reject("singular node transform");

      const auto& mesh = *node.mesh;
      for(size_t pi=0; pi<mesh.primitives_count; ++pi) {
        const auto& p = mesh.primitives[pi];
        if(p.type!=cgltf_primitive_type_triangles)
          reject("only triangle primitives are supported");
        const auto* pos = findAttribute(p,cgltf_attribute_type_position);
        const auto* nrm = findAttribute(p,cgltf_attribute_type_normal);
        if(pos==nullptr || nrm==nullptr)
          reject("POSITION and NORMAL are required");
        const auto* uv    = findAttribute(p,cgltf_attribute_type_texcoord);
        const auto* col   = findAttribute(p,cgltf_attribute_type_color);
        const auto* vid   = findAttribute(p,cgltf_attribute_type_custom,"_VERTEX");
        const auto* fid   = findAttribute(p,cgltf_attribute_type_custom,"_FEATURE");
        const size_t count = p.indices!=nullptr ? p.indices->count : pos->count;
        if(count%3!=0)
          reject("triangle index count is not a multiple of 3");
        const size_t tris = count/3;
        const size_t mat  = material(p.material);
        const auto extras = parseExtras(p.extras);
        const auto* order = extrasAccessor(d,extras.get(),"order",tris);
        const auto* flags = extrasAccessor(d,extras.get(),"flags",tris);
        const auto* light = extrasAccessor(d,extras.get(),"lightmap",tris);
        const int64_t local = nextLocal;
        nextLocal += int64_t(pos->count);

        auto corner = [&](size_t i) {
          Corner r {};
          float v[3], n[3], t[2] = {0,0};
          if(!cgltf_accessor_read_float(pos,i,v,3) || !cgltf_accessor_read_float(nrm,i,n,3))
            reject("cannot read POSITION or NORMAL");
          if(uv!=nullptr && !cgltf_accessor_read_float(uv,i,t,2))
            reject("cannot read TEXCOORD_0");
          if(raw) {
            r.position = {-v[0],v[1],v[2]};
            r.value.normal = {-n[0],n[1],n[2]};
            } else {
            for(unsigned ax=0; ax<3; ++ax) {
              const double w = double(m[ax])*v[0] + double(m[ax+4])*v[1] + double(m[ax+8])*v[2] + m[ax+12];
              r.position[ax] = float(w * (ax==0 ? -100.0 : 100.0));
              }
            double tn[3];
            for(unsigned ax=0; ax<3; ++ax)
              tn[ax] = (cof[ax*3]*n[0] + cof[ax*3+1]*n[1] + cof[ax*3+2]*n[2])/det;
            const double len = std::hypot(tn[0],tn[1],tn[2]);
            if(!std::isfinite(len) || len==0)
              reject("NORMAL must be finite and non-zero");
            r.value.normal = {float(-tn[0]/len),float(tn[1]/len),float(tn[2]/len)};
            }
          r.value.texture = {t[0],t[1]};
          r.value.light = 0xFFFFFFFFu;
          if(col!=nullptr) {
            if(col->component_type==cgltf_component_type_r_8u && col->type==cgltf_type_vec4 && col->normalized) {
              uint8_t rgba[4];
              cgltf_uint u[4];
              if(!cgltf_accessor_read_uint(col,i,u,4))
                reject("cannot read COLOR_0");
              for(unsigned ch=0; ch<4; ++ch)
                rgba[ch] = uint8_t(u[ch]);
              r.value.light = rgbaToLight(rgba);
              } else {
              float cf[4] = {1,1,1,1};
              const size_t comps = col->type==cgltf_type_vec3 ? 3 : 4;
              if(!cgltf_accessor_read_float(col,i,cf,comps))
                reject("cannot read COLOR_0");
              uint8_t rgba[4];
              for(unsigned ch=0; ch<4; ++ch)
                rgba[ch] = uint8_t(std::lround(std::clamp(double(cf[ch]),0.0,1.0)*255.0));
              r.value.light = rgbaToLight(rgba);
              }
            }
          auto sourceId = [&](const cgltf_accessor* a) -> int64_t {
            if(a==nullptr)
              return -1 - (local + int64_t(i));
            float id;
            if(!cgltf_accessor_read_float(a,i,&id,1) || !(id>=0) || id>=float(exactIndexLimit) || id!=std::floor(id))
              reject("invalid _VERTEX/_FEATURE value");
            return int64_t(id);
            };
          // Edited geometry without source ids gets negative ids unique per
          // chunk vertex, so it never merges with source vertices.
          r.vertex  = {sourceId(vid),bits(r.position.x),bits(r.position.y),bits(r.position.z)};
          r.feature = {sourceId(fid),bits(r.value.texture.x),bits(r.value.texture.y),r.value.light,
                       bits(r.value.normal.x),bits(r.value.normal.y),bits(r.value.normal.z)};
          return r;
          };

        for(size_t t=0; t<tris; ++t) {
          Triangle tri {};
          tri.material = mat;
          tri.order    = -1;
          tri.lightmap = -1;
          for(unsigned c=0; c<3; ++c) {
            const size_t i = p.indices!=nullptr ? cgltf_accessor_read_index(p.indices,t*3+c) : t*3+c;
            if(i>=pos->count)
              reject("triangle index outside POSITION");
            tri.corners[c] = corner(i);
            }
          if(!raw && det<0)
            std::swap(tri.corners[1],tri.corners[2]);
          if(order!=nullptr) {
            float o;
            if(!cgltf_accessor_read_float(order,t,&o,1) || !(o>=0) || o>=float(exactIndexLimit) || o!=std::floor(o))
              reject("invalid order value");
            tri.order = int64_t(o);
            }
          if(flags!=nullptr) {
            cgltf_uint f[2];
            if(!cgltf_accessor_read_uint(flags,t,f,2))
              reject("cannot read flags");
            tri.flags = unpackFlags(uint16_t(f[0]),uint16_t(f[1]));
            }
          if(light!=nullptr) {
            float l;
            if(!cgltf_accessor_read_float(light,t,&l,1) || l!=std::floor(l))
              reject("invalid lightmap value");
            tri.lightmap = int32_t(l);
            }
          triangles.push_back(tri);
          }
        }
      }
  };

}

uint32_t worldChunkCrc32(const void* data, size_t size, uint32_t crc) {
  static const auto table = [] {
    std::array<uint32_t,256> t {};
    for(uint32_t i=0; i<256; ++i) {
      uint32_t c = i;
      for(int k=0; k<8; ++k)
        c = (c&1u) ? 0xEDB88320u ^ (c>>1) : c>>1;
      t[i] = c;
      }
    return t;
    }();
  crc = ~crc;
  const auto* p = static_cast<const uint8_t*>(data);
  for(size_t i=0; i<size; ++i)
    crc = table[(crc ^ p[i]) & 0xFFu] ^ (crc>>8);
  return ~crc;
  }

zenkit::Mesh canonicalWorldMesh(const zenkit::Mesh& mesh) {
  checkPolygons(mesh);
  const auto& p = mesh.polygons;
  const size_t n = p.material_indices.size();

  std::vector<uint32_t> used(mesh.materials.size(),0);
  for(const auto m:p.material_indices)
    used[m] = 1;
  std::vector<uint32_t> materialMap(mesh.materials.size(),0);
  zenkit::Mesh out;
  for(size_t i=0; i<mesh.materials.size(); ++i)
    if(used[i]) {
      materialMap[i] = uint32_t(out.materials.size());
      out.materials.push_back(mesh.materials[i]);
      }

  std::vector<uint32_t> vertexMap(mesh.vertices.size(),UINT32_MAX), featureMap(mesh.features.size(),UINT32_MAX);
  auto& q = out.polygons;
  q.material_indices.reserve(n);
  q.lightmap_indices = p.lightmap_indices;
  q.flags            = p.flags;
  q.vertex_indices.reserve(n*3);
  q.feature_indices.reserve(n*3);
  for(size_t i=0; i<n; ++i) {
    q.material_indices.push_back(materialMap[p.material_indices[i]]);
    for(unsigned c=0; c<3; ++c) {
      auto& v = vertexMap[p.vertex_indices[i*3+c]];
      if(v==UINT32_MAX) {
        v = uint32_t(out.vertices.size());
        out.vertices.push_back(mesh.vertices[p.vertex_indices[i*3+c]]);
        }
      q.vertex_indices.push_back(v);
      auto& f = featureMap[p.feature_indices[i*3+c]];
      if(f==UINT32_MAX) {
        f = uint32_t(out.features.size());
        out.features.push_back(mesh.features[p.feature_indices[i*3+c]]);
        }
      q.feature_indices.push_back(f);
      }
    }
  return out;
  }

WorldChunkManifest writeWorldChunks(const zenkit::Mesh& mesh, const WorldChunkSource& source,
                                    const std::filesystem::path& dir, int32_t chunkSize) {
  checkPolygons(mesh);
  if(chunkSize<=0)
    reject("chunk size must be positive");
  const auto& p = mesh.polygons;
  const size_t n = p.material_indices.size();
  if(n>=exactIndexLimit || mesh.vertices.size()>=exactIndexLimit || mesh.features.size()>=exactIndexLimit)
    reject("mesh too large for exact float indices");

  // Triangle -> chunk by centroid in the ground plane (Gothic X and Z).
  std::map<std::pair<int32_t,int32_t>,std::vector<uint32_t>> chunks;
  for(size_t i=0; i<n; ++i) {
    double cx = 0, cz = 0;
    for(unsigned c=0; c<3; ++c) {
      const auto& v = mesh.vertices[p.vertex_indices[i*3+c]];
      cx += v.x;
      cz += v.z;
      }
    chunks[{chunkCoord(float(cx/3.0),chunkSize),chunkCoord(float(cz/3.0),chunkSize)}].push_back(uint32_t(i));
    }

  std::filesystem::create_directories(dir/"chunks");
  WorldChunkManifest manifest;
  manifest.source    = source;
  manifest.chunkSize = chunkSize;
  manifest.triangles = n;

  for(const auto& [coord,tris]:chunks) {
    // Chunk vertex = (source vertex, source feature) pair, first use order.
    std::map<std::pair<uint32_t,uint32_t>,uint32_t> local;
    std::vector<float>   position, normal, uv, vid, fid;
    std::vector<uint8_t> color;
    std::map<uint32_t,std::vector<uint32_t>> byMaterial;
    for(const auto t:tris)
      byMaterial[p.material_indices[t]].push_back(t);

    auto vertexOf = [&](uint32_t v, uint32_t f) {
      auto [it,inserted] = local.emplace(std::make_pair(v,f),uint32_t(local.size()));
      if(inserted) {
        const auto& pv = mesh.vertices[v];
        const auto& pf = mesh.features[f];
        const float gp[3] = {-pv.x,pv.y,pv.z};
        for(unsigned ax=0; ax<3; ++ax) {
          position.push_back(gp[ax]);
          }
        normal.push_back(-pf.normal.x);
        normal.push_back(pf.normal.y);
        normal.push_back(pf.normal.z);
        uv.push_back(pf.texture.x);
        uv.push_back(pf.texture.y);
        for(const auto ch:lightToRgba(pf.light))
          color.push_back(ch);
        vid.push_back(float(v));
        fid.push_back(float(f));
        }
      return it->second;
      };

    // Build indices first: the vertex table is shared by all primitives.
    struct Prim { uint32_t material; std::vector<uint32_t> idx; std::vector<float> order, lightmap; std::vector<uint16_t> flags; };
    std::vector<Prim> prims;
    for(const auto& [mat,list]:byMaterial) {
      Prim pr;
      pr.material = mat;
      for(const auto t:list) {
        for(unsigned c=0; c<3; ++c)
          pr.idx.push_back(vertexOf(p.vertex_indices[t*3+c],p.feature_indices[t*3+c]));
        pr.order.push_back(float(t));
        const auto f = packFlags(p.flags[t]);
        pr.flags.push_back(f[0]);
        pr.flags.push_back(f[1]);
        pr.lightmap.push_back(float(p.lightmap_indices[t]));
        }
      prims.push_back(std::move(pr));
      }

    GlbBuilder glb;
    // POSITION min/max are required by glTF: bounds of the stored values.
    std::string minMax;
    {
    float l[3] = {position[0],position[1],position[2]}, h[3] = {position[0],position[1],position[2]};
    for(size_t i=0; i<position.size(); i+=3)
      for(unsigned ax=0; ax<3; ++ax) {
        l[ax] = std::min(l[ax],position[i+ax]);
        h[ax] = std::max(h[ax],position[i+ax]);
        }
    minMax = ",\"min\":[" + jsonFloat(l[0]) + "," + jsonFloat(l[1]) + "," + jsonFloat(l[2]) +
             "],\"max\":[" + jsonFloat(h[0]) + "," + jsonFloat(h[1]) + "," + jsonFloat(h[2]) + "]";
    }
    const size_t aPos = glb.accessor(position,5126,"VEC3",3,false,34962,minMax);
    const size_t aNrm = glb.accessor(normal,5126,"VEC3",3,false,34962);
    const size_t aUv  = glb.accessor(uv,5126,"VEC2",2,false,34962);
    const size_t aCol = glb.accessor(color,5121,"VEC4",4,true,34962);
    const size_t aVid = glb.accessor(vid,5126,"SCALAR",1,false,34962);
    const size_t aFid = glb.accessor(fid,5126,"SCALAR",1,false,34962);
    const std::string attributes = "{\"POSITION\":" + std::to_string(aPos) + ",\"NORMAL\":" + std::to_string(aNrm) +
                                   ",\"TEXCOORD_0\":" + std::to_string(aUv) + ",\"COLOR_0\":" + std::to_string(aCol) +
                                   ",\"_VERTEX\":" + std::to_string(aVid) + ",\"_FEATURE\":" + std::to_string(aFid) + "}";
    std::string materials = "[", primitives = "[";
    for(size_t i=0; i<prims.size(); ++i) {
      const auto& pr = prims[i];
      const size_t aIdx = glb.accessor(pr.idx,5125,"SCALAR",1,false,34963);
      const size_t aOrd = glb.accessor(pr.order,5126,"SCALAR",1,false,0);
      const size_t aFlg = glb.accessor(pr.flags,5123,"VEC2",2,false,0);
      const size_t aLm  = glb.accessor(pr.lightmap,5126,"SCALAR",1,false,0);
      if(i>0) {
        materials += ",";
        primitives += ",";
        }
      const auto& m = mesh.materials[pr.material];
      materials += "{\"name\":" + jsonString(m.name) + ",\"extras\":{\"gothic\":" + materialJson(m,pr.material) + "}}";
      primitives += "{\"attributes\":" + attributes + ",\"indices\":" + std::to_string(aIdx) +
                    ",\"material\":" + std::to_string(i) + ",\"mode\":4,\"extras\":{\"gothic\":{\"order\":" +
                    std::to_string(aOrd) + ",\"flags\":" + std::to_string(aFlg) + ",\"lightmap\":" + std::to_string(aLm) + "}}}";
      }
    materials += "]";
    primitives += "]";

    const auto name = chunkName(coord.first,coord.second);
    const std::string json =
      "{\"asset\":{\"version\":\"2.0\",\"generator\":\"gothic-ai worldchunks\",\"extras\":{\"gothic\":{\"format\":\"" +
      std::string(chunkFormat) + "\",\"version\":" + std::to_string(formatVersion) + ",\"world\":" + jsonString(source.world) +
      ",\"chunk\":[" + std::to_string(coord.first) + "," + std::to_string(coord.second) + "]}}}," +
      "\"scene\":0,\"scenes\":[{\"nodes\":[0]}]," +
      "\"nodes\":[{\"name\":" + jsonString(name) + ",\"mesh\":0,\"scale\":[0.01,0.01,0.01]}]," +
      "\"meshes\":[{\"name\":" + jsonString(name) + ",\"primitives\":" + primitives + "}]," +
      "\"materials\":" + materials + "," +
      "\"accessors\":" + glb.accessorsJson() + "," +
      "\"bufferViews\":" + glb.viewsJson() + "," +
      "\"buffers\":[{\"byteLength\":" + std::to_string(glb.bin.size()) + "}]}";
    const auto bytes = glb.finish(json);
    const auto file = std::string("chunks/") + name + ".glb";
    writeFile(dir/file,bytes.data(),bytes.size());
    manifest.chunks.push_back(file);
    }

  std::string m = "{\n  \"format\": \"" + std::string(worldFormat) + "\",\n  \"version\": " + std::to_string(formatVersion) +
                  ",\n  \"source\": {\"world\": " + jsonString(source.world) + ", \"size\": " + std::to_string(source.size) +
                  ", \"crc32\": " + std::to_string(source.crc32) + "},\n  \"chunk_size_cm\": " + std::to_string(chunkSize) +
                  ",\n  \"triangles\": " + std::to_string(n) + ",\n  \"chunks\": [";
  for(size_t i=0; i<manifest.chunks.size(); ++i)
    m += std::string(i==0 ? "\n    " : ",\n    ") + jsonString(manifest.chunks[i]);
  m += "\n  ]\n}\n";
  writeFile(dir/manifestName,m.data(),m.size());
  return manifest;
  }

WorldChunkManifest readWorldChunkManifest(const std::filesystem::path& dir) {
  const auto bytes = readFile(dir/manifestName);
  rapidjson::Document doc;
  doc.Parse<rapidjson::kParseFullPrecisionFlag>(reinterpret_cast<const char*>(bytes.data()),bytes.size());
  if(doc.HasParseError() || !doc.IsObject())
    reject("invalid " + (dir/manifestName).string());
  if(jsonToString(member(doc,"format"),"format")!=worldFormat)
    reject("not a converted world: " + dir.string());
  if(jsonToInt(member(doc,"version"),"version")!=formatVersion)
    reject("unsupported world format version in " + dir.string());
  WorldChunkManifest m;
  const auto& s = member(doc,"source");
  m.source.world = jsonToString(member(s,"world"),"world");
  m.source.size  = uint64_t(jsonToInt(member(s,"size"),"size"));
  m.source.crc32 = uint32_t(jsonToInt(member(s,"crc32"),"crc32"));
  m.chunkSize    = int32_t(jsonToInt(member(doc,"chunk_size_cm"),"chunk_size_cm"));
  m.triangles    = uint64_t(jsonToInt(member(doc,"triangles"),"triangles"));
  const auto& c  = member(doc,"chunks");
  if(!c.IsArray())
    reject("chunks must be an array");
  for(const auto& v:c.GetArray()) {
    auto name = jsonToString(v,"chunks");
    const std::filesystem::path rel(name);
    if(rel.is_absolute() || rel.empty())
      reject("chunk path must be relative: " + name);
    for(const auto& part:rel)
      if(part=="..")
        reject("chunk path must stay inside the world directory: " + name);
    m.chunks.push_back(std::move(name));
    }
  return m;
  }

zenkit::Mesh readWorldChunks(const std::filesystem::path& dir, const std::filesystem::path& overrideDir) {
  const auto manifest = readWorldChunkManifest(dir);
  ChunkReader reader;
  for(const auto& chunk:manifest.chunks) {
    auto path = dir/chunk;
    if(!overrideDir.empty() && std::filesystem::exists(overrideDir/chunk))
      path = overrideDir/chunk;
    reader.read(path);
    }

  // Source triangles in source order, then edited ones in chunk order.
  std::vector<size_t> ordered, edited;
  std::set<int64_t> seen;
  for(size_t i=0; i<reader.triangles.size(); ++i) {
    const auto o = reader.triangles[i].order;
    if(o<0) {
      edited.push_back(i);
      continue;
      }
    if(!seen.insert(o).second)
      reject("triangle order " + std::to_string(o) + " appears twice");
    ordered.push_back(i);
    }
  std::stable_sort(ordered.begin(),ordered.end(),[&](size_t a, size_t b) {
    return reader.triangles[a].order<reader.triangles[b].order;
    });
  ordered.insert(ordered.end(),edited.begin(),edited.end());

  // Source materials keep their relative order, new ones follow.
  std::vector<size_t> matOrder(reader.materials.size());
  for(size_t i=0; i<matOrder.size(); ++i)
    matOrder[i] = i;
  std::stable_sort(matOrder.begin(),matOrder.end(),[&](size_t a, size_t b) {
    const auto ia = reader.materials[a].index, ib = reader.materials[b].index;
    if((ia<0)!=(ib<0))
      return ia>=0;
    return ia>=0 && ia<ib;
    });
  std::vector<uint32_t> usedMat(reader.materials.size(),0);
  for(const auto t:ordered)
    usedMat[reader.triangles[t].material] = 1;
  std::vector<uint32_t> matMap(reader.materials.size(),UINT32_MAX);
  zenkit::Mesh out;
  for(const auto i:matOrder)
    if(usedMat[i]) {
      matMap[i] = uint32_t(out.materials.size());
      out.materials.push_back(reader.materials[i].material);
      }

  std::map<VertexKey,uint32_t>  vertices;
  std::map<FeatureKey,uint32_t> features;
  auto& q = out.polygons;
  q.material_indices.reserve(ordered.size());
  q.lightmap_indices.reserve(ordered.size());
  q.flags.reserve(ordered.size());
  q.vertex_indices.reserve(ordered.size()*3);
  q.feature_indices.reserve(ordered.size()*3);
  for(const auto t:ordered) {
    const auto& tri = reader.triangles[t];
    q.material_indices.push_back(matMap[tri.material]);
    q.lightmap_indices.push_back(tri.lightmap);
    q.flags.push_back(tri.flags);
    for(const auto& c:tri.corners) {
      auto [v,newVertex] = vertices.emplace(c.vertex,uint32_t(out.vertices.size()));
      if(newVertex)
        out.vertices.push_back(c.position);
      q.vertex_indices.push_back(v->second);
      auto [f,newFeature] = features.emplace(c.feature,uint32_t(out.features.size()));
      if(newFeature)
        out.features.push_back(c.value);
      q.feature_indices.push_back(f->second);
      }
    }
  return out;
  }

std::string compareWorldMeshes(const zenkit::Mesh& e, const zenkit::Mesh& a) {
  if(e.materials.size()!=a.materials.size())
    return "materials: " + std::to_string(e.materials.size()) + " vs " + std::to_string(a.materials.size());
  for(size_t i=0; i<e.materials.size(); ++i)
    if(!sameMaterial(e.materials[i],a.materials[i]))
      return "material " + std::to_string(i) + " (" + e.materials[i].name + ") differs";
  if(e.vertices.size()!=a.vertices.size())
    return "vertices: " + std::to_string(e.vertices.size()) + " vs " + std::to_string(a.vertices.size());
  for(size_t i=0; i<e.vertices.size(); ++i)
    for(unsigned ax=0; ax<3; ++ax)
      if(bits(e.vertices[i][ax])!=bits(a.vertices[i][ax]))
        return "vertex " + std::to_string(i) + " differs";
  if(e.features.size()!=a.features.size())
    return "features: " + std::to_string(e.features.size()) + " vs " + std::to_string(a.features.size());
  for(size_t i=0; i<e.features.size(); ++i) {
    const auto& x = e.features[i];
    const auto& y = a.features[i];
    if(bits(x.texture.x)!=bits(y.texture.x) || bits(x.texture.y)!=bits(y.texture.y) || x.light!=y.light ||
       bits(x.normal.x)!=bits(y.normal.x) || bits(x.normal.y)!=bits(y.normal.y) || bits(x.normal.z)!=bits(y.normal.z))
      return "feature " + std::to_string(i) + " differs";
    }
  const auto& p = e.polygons;
  const auto& q = a.polygons;
  if(p.material_indices!=q.material_indices)
    return "polygon materials differ";
  if(p.vertex_indices!=q.vertex_indices)
    return "polygon vertex indices differ";
  if(p.feature_indices!=q.feature_indices)
    return "polygon feature indices differ";
  if(p.lightmap_indices!=q.lightmap_indices)
    return "polygon lightmap indices differ";
  if(p.flags.size()!=q.flags.size())
    return "polygon flags differ in count";
  for(size_t i=0; i<p.flags.size(); ++i)
    if(!(p.flags[i]==q.flags[i]))
      return "polygon flags " + std::to_string(i) + " differ";
  return {};
  }
