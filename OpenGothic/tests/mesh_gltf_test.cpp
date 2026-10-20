#include "commandline.h"
#include "gltfmesh.h"
#include "graphics/shaders.h"
#include "graphics/mesh/protomesh.h"
#include "graphics/mesh/submesh/packedmesh.h"
#include "graphics/mesh/submesh/pfxemittermesh.h"
#include "physics/fracture.h"
#include "resources.h"

#include <Tempest/Device>
#include <Tempest/Log>
#include <Tempest/VulkanApi>
#include <zenkit/Stream.hh>
#include <rapidjson/document.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>
#include <sys/wait.h>
#include <unistd.h>

namespace {
size_t assertions = 0;

void require(bool condition, std::string_view message) {
  ++assertions;
  if(!condition)
    throw std::runtime_error(std::string(message));
  }

bool close(float a, float b) { return std::abs(a-b)<.001f; }

void textFile(const std::filesystem::path& path, std::string_view text) {
  std::ofstream out(path,std::ios::binary);
  out.write(text.data(),std::streamsize(text.size()));
  if(!out)
    throw std::runtime_error("unable to write mesh fixture");
  }

void word(std::vector<uint8_t>& bytes, uint32_t value) {
  for(size_t i=0;i<4;++i)
    bytes.push_back(uint8_t(value>>(i*8)));
  }

void real(std::vector<uint8_t>& bytes, float value) {
  uint32_t bits = 0;
  static_assert(sizeof(bits)==sizeof(value));
  std::memcpy(&bits,&value,sizeof(bits));
  word(bytes,bits);
  }

struct GlbOptions {
  size_t positions = 3;
  uint32_t indexType = 5123;
  bool indexed = true;
  bool normals = true;
  bool uv = true;
  bool transformed = false;
  bool mirrored = false;
  bool rotated = false;
  bool skin = false;
  bool external = false;
  bool sparse = false;
  bool morph = false;
  bool requiredExtension = false;
  bool material = true;
  bool namedMaterial = true;
  bool mixedAlpha = false;
  bool wedgeOverflow = false;
  std::string alpha = "OPAQUE";
  std::string group = "WOOD";
  std::string texture = "OWN_TEST_BARREL_WOOD";
  };

// This off-centre triangle distinguishes the X mirror, units, winding,
// accumulated node transforms and original fallback without any game assets.
void glbFile(const std::filesystem::path& path, const GlbOptions& options = {}) {
  std::vector<uint8_t> binary;
  const std::array<std::array<float,3>,3> positions = {{{1,2,3},{4,2,3},{1,6,3}}};
  for(size_t i=0;i<options.positions;++i)
    for(float component:positions[i%3])
      real(binary,component);
  const size_t normalOffset = binary.size();
  for(size_t i=0;i<options.positions;++i)
    for(float component:{0.f,0.f,1.f})
      real(binary,component);
  const size_t uvOffset = binary.size();
  const std::array<std::array<float,2>,3> uv = {{{.125f,.25f},{.875f,.25f},{.125f,.75f}}};
  for(size_t i=0;i<options.positions;++i)
    for(float component:uv[i%3])
      real(binary,component);
  const size_t indexOffset = binary.size();
  for(uint32_t i=0;i<3;++i) {
    binary.push_back(uint8_t(i));
    if(options.indexType>=5123)
      binary.push_back(0);
    if(options.indexType==5125) {
      binary.push_back(0);
      binary.push_back(0);
      }
    }
  const size_t indexLength = binary.size()-indexOffset;
  const size_t bufferLength = binary.size();
  while(binary.size()%4!=0)
    binary.push_back(0);

  const auto count = std::to_string(options.positions);
  std::string json = R"({"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[0]}],)";
  if(options.transformed || options.mirrored) {
    json += "\"nodes\":[{\"scale\":["+std::string(options.mirrored ? "-2" : "2")+
            R"(,3,4],"children":[1]},{"translation":[1,2,3],"mesh":0)";
    }
  else
    json += R"("nodes":[{"mesh":0)";
  if(options.rotated)
    json += R"(,"rotation":[0.3826834323650898,0,0,0.9238795325112867])";
  if(options.skin)
    json += R"(,"skin":0)";
  json += "}],";
  if(options.skin)
    json += R"("skins":[{"joints":[0]}],)";
  if(options.requiredExtension)
    json += R"("extensionsRequired":["UNSUPPORTED_mesh_test"],)";
  json += "\"buffers\":[{\"byteLength\":"+std::to_string(bufferLength);
  if(options.external)
    json += R"(,"uri":"external.bin")";
  json += "}],\"bufferViews\":[{\"buffer\":0,\"byteOffset\":0,\"byteLength\":"+
          std::to_string(normalOffset)+"},{\"buffer\":0,\"byteOffset\":"+
          std::to_string(normalOffset)+",\"byteLength\":"+std::to_string(uvOffset-normalOffset)+
          "},{\"buffer\":0,\"byteOffset\":"+std::to_string(uvOffset)+",\"byteLength\":"+
          std::to_string(indexOffset-uvOffset)+"},{\"buffer\":0,\"byteOffset\":"+
          std::to_string(indexOffset)+",\"byteLength\":"+std::to_string(indexLength)+"}],";
  json += "\"accessors\":[{\"bufferView\":0,\"componentType\":5126,\"count\":"+count+
          R"(,"type":"VEC3","min":[1,2,3],"max":[4,6,3])";
  if(options.sparse)
    json += R"(,"sparse":{"count":1,"indices":{"bufferView":3,"componentType":5121},"values":{"bufferView":0}})";
  json += "},{\"bufferView\":1,\"componentType\":5126,\"count\":"+count+
          ",\"type\":\"VEC3\"},{\"bufferView\":2,\"componentType\":5126,\"count\":"+count+
          ",\"type\":\"VEC2\"},{\"bufferView\":3,\"componentType\":"+
          std::to_string(options.indexType)+R"(,"count":3,"type":"SCALAR"})";
  if(options.wedgeOverflow)
    json += ",{\"bufferView\":1,\"componentType\":5126,\"count\":"+count+",\"type\":\"VEC3\"}";
  json += R"(],"materials":[{)";
  if(options.namedMaterial)
    json += "\"name\":\""+options.texture+"\",";
  json += "\"alphaMode\":\""+options.alpha+"\",\"extras\":{\"group\":\""+options.group+"\"}}";
  if(options.mixedAlpha)
    json += R"(,{"name":"OWN_TEST_MASK.TGA","alphaMode":"MASK"})";
  json += R"(],"meshes":[{"primitives":[{"attributes":{"POSITION":0)";
  if(options.normals)
    json += R"(,"NORMAL":1)";
  if(options.uv)
    json += R"(,"TEXCOORD_0":2)";
  json += "}";
  if(options.indexed)
    json += R"(,"indices":3)";
  if(options.material)
    json += R"(,"material":0)";
  if(options.morph)
    json += R"(,"targets":[{"POSITION":0}])";
  json += R"(,"mode":4})";
  if(options.mixedAlpha)
    json += R"(,{"attributes":{"POSITION":0,"NORMAL":1,"TEXCOORD_0":2},"indices":3,"material":1,"mode":4})";
  if(options.wedgeOverflow)
    json += R"(,{"attributes":{"POSITION":0,"NORMAL":4,"TEXCOORD_0":2},"indices":3,"material":0,"mode":4})";
  json += "]}]}";
  while(json.size()%4!=0)
    json.push_back(' ');
  std::vector<uint8_t> bytes;
  word(bytes,0x46546c67);
  word(bytes,2);
  word(bytes,uint32_t(12+8+json.size()+8+binary.size()));
  word(bytes,uint32_t(json.size()));
  word(bytes,0x4e4f534a);
  bytes.insert(bytes.end(),json.begin(),json.end());
  word(bytes,uint32_t(binary.size()));
  word(bytes,0x004e4942);
  bytes.insert(bytes.end(),binary.begin(),binary.end());
  std::ofstream out(path,std::ios::binary);
  out.write(reinterpret_cast<const char*>(bytes.data()),std::streamsize(bytes.size()));
  if(!out)
    throw std::runtime_error("unable to write GLB fixture");
  }

void originalFile(const std::filesystem::path& path) {
  zenkit::MultiResolutionMesh mesh;
  mesh.positions = {{10,20,30},{14,20,30},{10,25,30}};
  mesh.normals = {{0,0,1},{0,0,1},{0,0,1}};
  mesh.bbox = {{10,20,30},{14,25,30}};
  mesh.obbox.center = {12,22.5f,30};
  mesh.obbox.axes[0] = {1,0,0};
  mesh.obbox.axes[1] = {0,1,0};
  mesh.obbox.axes[2] = {0,0,1};
  mesh.obbox.half_width = {2,2.5f,0};
  mesh.sub_meshes.resize(1);
  auto& surface = mesh.sub_meshes[0];
  surface.mat.name = "ORIGINAL";
  surface.mat.texture = "ORIGINAL.TGA";
  surface.mat.group = zenkit::MaterialGroup::STONE;
  surface.mat.color = {255,255,255,255};
  surface.mat.alpha_func = zenkit::AlphaFunction::NONE;
  surface.wedges = {{{0,0,1},{0,0},0},{{0,0,1},{1,0},1},{{0,0,1},{0,1},2}};
  surface.triangles = {{{0,2,1}}};
  std::vector<std::byte> bytes;
  auto writer = zenkit::Write::to(&bytes);
  mesh.save(writer.get(),zenkit::GameVersion::GOTHIC_2);
  writer.reset();

  // This vendored ZenKit writer includes the size word in content_size,
  // serializes section byte lengths where its reader expects element counts,
  // and writes a 10-byte tail where the reader consumes 16. Normalize only
  // our synthetic fixture; production MRM loading and the dependency stay as-is.
  auto readWord = [&](size_t offset) {
    uint32_t value = 0;
    for(size_t i=0;i<4;++i)
      value |= uint32_t(std::to_integer<uint8_t>(bytes.at(offset+i)))<<(i*8);
    return value;
    };
  auto writeWord = [&](size_t offset, uint32_t value) {
    for(size_t i=0;i<4;++i)
      bytes.at(offset+i) = std::byte(value>>(i*8));
    };
  // Chunk header: type:u16, body_size:u32, version:u16, content_size:u32.
  const size_t table = 8+readWord(8);
  const uint32_t chunkSize = readWord(2);
  const size_t meshEnd = 6+size_t(chunkSize);
  writeWord(8,readWord(8)-4);
  // table: submesh_count:u8, four position/normal words, then section pairs.
  writeWord(table+21,uint32_t(surface.triangles.size()));
  writeWord(table+29,uint32_t(surface.wedges.size()));
  bytes.insert(bytes.begin()+std::ptrdiff_t(meshEnd),6,std::byte{});
  writeWord(2,chunkSize+6);
  {
    std::ofstream out(path,std::ios::binary);
    out.write(reinterpret_cast<const char*>(bytes.data()),std::streamsize(bytes.size()));
    if(!out)
      throw std::runtime_error("unable to write original MRM fixture");
    }
  auto reader = zenkit::Read::from(path);
  zenkit::MultiResolutionMesh restored;
  restored.load(reader.get());
  if(restored.positions.size()!=3 || restored.sub_meshes.size()!=1 ||
     restored.sub_meshes[0].triangles.size()!=1 || restored.sub_meshes[0].wedges.size()!=3 ||
     restored.sub_meshes[0].mat.texture!="ORIGINAL.TGA" ||
     restored.sub_meshes[0].mat.group!=zenkit::MaterialGroup::STONE)
    throw std::runtime_error("invalid original MRM fixture roundtrip");
  }

struct Fixture {
  std::filesystem::path root;
  Fixture() {
    std::string pattern = (std::filesystem::temp_directory_path()/"gothic-mesh-gltf-XXXXXX").string();
    auto* directory = mkdtemp(pattern.data());
    if(directory==nullptr)
      throw std::runtime_error("unable to create mesh fixture directory");
    root = directory;
    std::filesystem::create_directories(root/"meshes/test");
    std::filesystem::create_directories(root/"_work");
    originalFile(root/"_work/OWN_MESH.MRM");
    glbFile(root/"meshes/test/own.glb");
    setenv("OPENGOTHIC_CONTENT",root.c_str(),1);
    setenv("OPENGOTHIC_TEXTURES","",1);
    setenv("OPENGOTHIC_CENSUS",(root/"census.json").c_str(),1);
    }
  ~Fixture() {
    std::error_code error;
    std::filesystem::remove_all(root,error);
    }
  void index(std::string_view body = R"({"own_mesh":{"glb":"test/own.glb"}})") const {
    textFile(root/"meshes/meshes.json",body);
    }
  void indexPath(std::string_view path) const {
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> json(buffer);
    json.StartObject(); json.Key("OWN_MESH"); json.StartObject(); json.Key("glb");
    json.String(path.data(),rapidjson::SizeType(path.size()));
    json.EndObject(); json.EndObject();
    index(std::string_view(buffer.GetString(),buffer.GetSize()));
    }
  rapidjson::Document census() const {
    require(Resources::writeCensus(),"mesh census must be writable");
    std::ifstream in(root/"census.json",std::ios::binary);
    const std::string text{std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
    rapidjson::Document document;
    document.Parse(text.data(),text.size());
    require(!document.HasParseError() && document.IsObject(),"mesh census must contain valid JSON");
    require(document.HasMember("files") && document["files"].IsArray(),"mesh census must list original opens");
    require(document.HasMember("replacements") && document["replacements"].IsArray(),"mesh census must list replacements");
    return document;
    }
  };

void checkCensus(const Fixture& fixture, bool own, uint64_t expectedOpens) {
  const auto document = fixture.census();
  uint64_t originalOpens = 0;
  uint64_t replacementOpens = 0;
  for(const auto& item:document["files"].GetArray()) {
    if(std::string_view(item["name"].GetString())=="OWN_MESH.MRM") {
      require(std::string_view(item["kind"].GetString())=="mesh","original MRM census kind must be mesh");
      originalOpens += item["opens"].GetUint64();
      }
    }
  for(const auto& item:document["replacements"].GetArray()) {
    require(std::string_view(item["name"].GetString())=="OWN_MESH.MRM","mesh census must use the original visual name");
    require(std::string_view(item["kind"].GetString())=="mesh","replacement census kind must be mesh");
    require(std::string_view(item["replacement"].GetString())=="meshes/test/own.glb",
            "replacement census must use its content-relative mesh path");
    replacementOpens += item["opens"].GetUint64();
    }
  require(originalOpens==(own ? 0 : expectedOpens),"only fallback may open the original MRM");
  require(replacementOpens==(own ? expectedOpens : 0),"only successful GLB loads may count as replacements");
  }

void resourcePaths(const Fixture& fixture, bool own, bool mountOriginal = true) {
  Resources resources;
  if(mountOriginal)
    Resources::mountWork(fixture.root/"_work");
  const auto* proto = Resources::loadMesh("own_mesh.3ds");
  require(proto!=nullptr,"visual mesh must load");
  const auto* bounds = proto->bbox();
  require(close(bounds[0].x,own ? -400.f : 10.f) && close(bounds[1].x,own ? -100.f : 14.f),
          "ProtoMesh X bbox must use mirrored centimetres, or the unchanged original");
  require(close(bounds[0].y,own ? 200.f : 20.f) && close(bounds[1].y,own ? 600.f : 25.f),
          "ProtoMesh Y bbox must use centimetres, or the unchanged original");
  require(close(bounds[0].z,own ? 300.f : 30.f) && close(bounds[1].z,own ? 300.f : 30.f),
          "ProtoMesh Z bbox must use centimetres, or the unchanged original");
  require(proto->attach.size()==1 && proto->attach[0].shape!=nullptr,"visual must retain its collision shape");
  require(proto->attach[0].sub.size()==1,"visual must retain its material surface");
  require(proto->attach[0].sub[0].texName==(own ? "OWN_TEST_BARREL_WOOD.TGA" : "ORIGINAL.TGA"),
          "ProtoMesh must retain the selected mesh's texture name");
  require(!proto->points.empty(),"visual must provide hit-zone and hull points");
  for(const auto& point:proto->points)
    require(own ? point.x<=-100.f : point.x>=10.f,"hit-zone points must come from the selected mesh");

  const auto* emitter = Resources::loadEmiterMesh("OWN_MESH.3DS");
  require(emitter!=nullptr,"emitter must load the same selected mesh");
  const auto point = emitter->randCoord(.1f,nullptr);
  require(close(point.z,own ? 300.f : 30.f) && (own ? point.x<=-100.f : point.x>=10.f),
          "emitter coordinates must use the GLB, or the original on fallback");

  // This is also ClothVisual::loadGeometry's source, before its cloth processing.
  bool replaced = false;
  const auto source = Resources::loadVisualMesh("OWN_MESH.MRM",&replaced);
  require(replaced==own,"shared geometry loader must report replacement only on success");
  require(source.sub_meshes.size()==1,"shared cloth geometry source must load");
  require(source.sub_meshes[0].mat.texture==(own ? "OWN_TEST_BARREL_WOOD.TGA" : "ORIGINAL.TGA"),
          "shared loader must retain the selected texture");
  require(source.sub_meshes[0].mat.group==(own ? zenkit::MaterialGroup::WOOD : zenkit::MaterialGroup::STONE),
          "shared loader must retain the selected physical material group");
  require(close(source.bbox.min.x,own ? -400.f : 10.f),"cloth source must select the same geometry");
  checkCensus(fixture,own,3);
  }

void frontFaces(const zenkit::MultiResolutionMesh& mesh) {
  size_t triangles = 0;
  // Original NW_HARBOUR_BARREL_01.MRM: 180/180 wedge normals in 60 triangles
  // have negative dot(cross(edge1,edge2), normal). PackedMesh retains winding;
  // PhysicVbo swaps indices 1/2 for Jolt. Keep glTF order after the X mirror,
  // reversing only for a negative node WORLD determinant (approved convention).
  for(const auto& surface:mesh.sub_meshes) {
    for(const auto& triangle:surface.triangles) {
      ++triangles;
      const auto& a = mesh.positions[surface.wedges[triangle.wedges[0]].index];
      const auto& b = mesh.positions[surface.wedges[triangle.wedges[1]].index];
      const auto& c = mesh.positions[surface.wedges[triangle.wedges[2]].index];
      const float nx = (b.y-a.y)*(c.z-a.z)-(b.z-a.z)*(c.y-a.y);
      const float ny = (b.z-a.z)*(c.x-a.x)-(b.x-a.x)*(c.z-a.z);
      const float nz = (b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
      for(uint16_t wedge:triangle.wedges) {
        const auto& normal = surface.wedges[wedge].normal;
        require(nx*normal.x+ny*normal.y+nz*normal.z<0.f,
                "every converted triangle must match the original MRM front-face convention");
        }
      }
    }
  require(triangles>0,"front-face fixture must contain real triangles");
  PackedMesh packed(mesh,PackedMesh::PK_Visual);
  for(size_t i=0;i<packed.indices.size();i+=3) {
    const auto& a = packed.vertices[packed.indices[i]];
    const auto& b = packed.vertices[packed.indices[i+1]];
    const auto& c = packed.vertices[packed.indices[i+2]];
    const float nx = (b.pos[1]-a.pos[1])*(c.pos[2]-a.pos[2])-(b.pos[2]-a.pos[2])*(c.pos[1]-a.pos[1]);
    const float ny = (b.pos[2]-a.pos[2])*(c.pos[0]-a.pos[0])-(b.pos[0]-a.pos[0])*(c.pos[2]-a.pos[2]);
    const float nz = (b.pos[0]-a.pos[0])*(c.pos[1]-a.pos[1])-(b.pos[1]-a.pos[1])*(c.pos[0]-a.pos[0]);
    if(nx==0.f && ny==0.f && nz==0.f)
      continue; // Meshlet padding is degenerate.
    require(nx*a.norm[0]+ny*a.norm[1]+nz*a.norm[2]<0.f,"PackedMesh must preserve MRM front faces");
    }
  }

void generatedBarrel(const Fixture& fixture) {
  const auto output = fixture.root/"generated-barrel";
  const pid_t child = fork();
  require(child>=0,"generator subprocess must start");
  if(child==0) {
    execlp("python3","python3",GOTHIC_TEST_BARREL_SCRIPT,output.c_str(),static_cast<char*>(nullptr));
    _exit(127);
    }
  int status = 0;
  require(waitpid(child,&status,0)==child,"generator subprocess must finish");
  require(WIFEXITED(status) && WEXITSTATUS(status)==0,"actual test barrel generator must succeed");
  const auto mesh = loadGltfMesh(output/"meshes/test/nw_harbour_barrel_01.glb");
  frontFaces(mesh);
  require(mesh.sub_meshes.size()==1 && mesh.sub_meshes[0].mat.texture=="OWN_TEST_BARREL_WOOD.TGA" &&
          mesh.sub_meshes[0].mat.group==zenkit::MaterialGroup::WOOD,
          "actual generated barrel must use the shared wood material and physical group");
  }

void converterCases(const Fixture& fixture) {
  const auto path = fixture.root/"meshes/test/converter.glb";
  for(uint32_t indexType:{5121u,5123u,5125u}) {
    GlbOptions options;
    options.indexType = indexType;
    glbFile(path,options);
    const auto mesh = loadGltfMesh(path);
    require(mesh.positions.size()==3 && mesh.sub_meshes.size()==1,"all unsigned index widths must load");
    require(mesh.sub_meshes[0].triangles.size()==1,"index accessors must retain one triangle");
    frontFaces(mesh);
    const auto& wedge = mesh.sub_meshes[0].wedges[0];
    require(close(wedge.normal.z,1.f),"converted normal must point +Z");
    require(close(wedge.texture.x,.125f) && close(wedge.texture.y,.25f),"UVs must remain unchanged");
    }
  GlbOptions options;
  options.indexed = false;
  options.uv = false;
  glbFile(path,options);
  auto mesh = loadGltfMesh(path);
  require(mesh.sub_meshes[0].triangles.size()==1,"non-indexed triangles must load");
  for(const auto& wedge:mesh.sub_meshes[0].wedges)
    require(close(wedge.texture.x,0.f) && close(wedge.texture.y,0.f),"missing UVs must default to zero");
  frontFaces(mesh);

  for(bool mirrored:{false,true}) {
    options = {};
    options.transformed = true;
    options.mirrored = mirrored;
    glbFile(path,options);
    mesh = loadGltfMesh(path);
    require(close(mesh.bbox.min.x,mirrored ? 400.f : -1000.f) &&
            close(mesh.bbox.max.x,mirrored ? 1000.f : -400.f) &&
            close(mesh.bbox.min.y,1200.f) && close(mesh.bbox.max.y,2400.f) &&
            close(mesh.bbox.min.z,2400.f) && close(mesh.bbox.max.z,2400.f),
            "child translation and parent scale must compose before axis and unit conversion");
    frontFaces(mesh);
    }
  options = {};
  options.transformed = true;
  options.rotated = true;
  glbFile(path,options);
  mesh = loadGltfMesh(path);
  frontFaces(mesh);
  for(const auto& wedge:mesh.sub_meshes[0].wedges)
    require(close(wedge.normal.x,0.f) && close(wedge.normal.y,-.8f) && close(wedge.normal.z,.6f),
            "normal must use the inverse transpose of the composed rotation and nonuniform scale");
  const std::array<std::pair<const char*,zenkit::MaterialGroup>,6> groups = {{
      {"UNDEF",zenkit::MaterialGroup::UNDEFINED},{"METAL",zenkit::MaterialGroup::METAL},
      {"STONE",zenkit::MaterialGroup::STONE},{"WOOD",zenkit::MaterialGroup::WOOD},
      {"EARTH",zenkit::MaterialGroup::EARTH},
      {"SNOW",zenkit::MaterialGroup::SNOW}}};
  for(const auto& group:groups) {
    options = {};
    options.group = group.first;
    options.texture = "NAMED_TEXTURE.TGA";
    glbFile(path,options);
    mesh = loadGltfMesh(path);
    require(mesh.sub_meshes[0].mat.group==group.second,"extras.group values must map to Gothic groups");
    require(mesh.sub_meshes[0].mat.texture=="NAMED_TEXTURE.TGA","existing texture extension must remain unchanged");
    }
  options = {};
  options.mixedAlpha = true;
  glbFile(path,options);
  mesh = loadGltfMesh(path);
  require(mesh.sub_meshes.size()==2,"OPAQUE and MASK surfaces must remain separate");
  require(mesh.sub_meshes[1].mat.group==zenkit::MaterialGroup::UNDEFINED,"missing group must default to UNDEF");
  Resources resources;
  PackedMesh packed(mesh,PackedMesh::PK_Visual,true);
  ProtoMesh proto(std::move(packed),"MIXED.MRM");
  require(proto.attach[0].sub.size()==2,"mixed alpha fixture must produce both surfaces");
  require(proto.attach[0].sub[0].material.alpha==Material::Solid,"OPAQUE must disable its surface's alpha test");
  require(proto.attach[0].sub[1].material.alpha==Material::AlphaTest,"MASK must enable its surface's alpha test");

  options = {};
  options.positions = 65535;
  glbFile(path,options);
  mesh = loadGltfMesh(path);
  require(mesh.positions.size()==65535 && mesh.sub_meshes[0].wedges.size()==65535,
          "exactly 65535 positions and wedges must remain valid");
  }

void invalidGlb(const Fixture& fixture, std::string_view name, const GlbOptions& options,
                std::string_view reason = {}) {
  const auto path = fixture.root/"meshes/test/own.glb";
  glbFile(path,options);
  bool rejected = false;
  try { loadGltfMesh(path); }
  catch(const std::runtime_error& error) {
    rejected = true;
    require(std::string_view(error.what()).size()>0,"GLB rejection must explain its reason");
    if(!reason.empty())
      require(std::string_view(error.what()).find(reason)!=std::string_view::npos,
              "GLB rejection must explain the unsupported material group");
    }
  require(rejected,"converter must reject "+std::string(name));
  fixture.index();
  resourcePaths(fixture,false);
  }

void rejectionCases(const Fixture& fixture) {
  fixture.index();
  textFile(fixture.root/"meshes/test/own.glb","broken glb");
  resourcePaths(fixture,false);
  std::filesystem::remove(fixture.root/"meshes/test/own.glb");
  resourcePaths(fixture,false);
  GlbOptions options;
  for(const auto* alpha:{"OPAQUE","MASK"}) {
    options = {};
    options.group = "WATER";
    options.alpha = alpha;
    invalidGlb(fixture,"WATER static material",options,"water belongs to world mesh surfaces and overrides OPAQUE/MASK");
    }
  options = {};
  options.skin = true; invalidGlb(fixture,"skin",options);
  options = {}; options.alpha = "BLEND"; invalidGlb(fixture,"BLEND",options);
  options = {}; options.normals = false; invalidGlb(fixture,"missing NORMAL",options);
  options = {}; options.external = true; invalidGlb(fixture,"external URI",options);
  options = {}; options.positions = 65536; invalidGlb(fixture,"more than 65535 positions",options);
  options = {}; options.positions = 32768; options.wedgeOverflow = true;
  invalidGlb(fixture,"more than 65535 wedges for one material",options);
  options = {}; options.sparse = true; invalidGlb(fixture,"sparse accessor",options);
  options = {}; options.morph = true; invalidGlb(fixture,"morph targets",options);
  options = {}; options.requiredExtension = true; invalidGlb(fixture,"required extension",options);
  options = {}; options.material = false; invalidGlb(fixture,"missing material",options);
  options = {}; options.namedMaterial = false; invalidGlb(fixture,"unnamed material",options);
  options = {}; options.texture = "OWN_TEST\\u0000BARREL"; invalidGlb(fixture,"NUL material name",options);
  }

void indexCases(const Fixture& fixture) {
  glbFile(fixture.root/"meshes/test/own.glb");
  std::filesystem::remove(fixture.root/"meshes/meshes.json");
  resourcePaths(fixture,false);
  fixture.index("{}");
  resourcePaths(fixture,false);
  fixture.index();
  setenv("OPENGOTHIC_CONTENT","",1);
  resourcePaths(fixture,false);
  setenv("OPENGOTHIC_CONTENT",fixture.root.c_str(),1);

  // All forbidden paths identify a readable GLB if validation is bypassed.
  fixture.indexPath((fixture.root/"meshes/test/own.glb").string());
  resourcePaths(fixture,false);
  for(const auto* path:{"../meshes/test/own.glb","test/../test/own.glb"}) {
    fixture.indexPath(path);
    resourcePaths(fixture,false);
    }
  glbFile(fixture.root/"meshes/test/own.MRM");
  fixture.indexPath("test/own.MRM");
  resourcePaths(fixture,false);
  fixture.index(R"({"OWN_MESH":{"glb":"test/own.glb\u0000suffix"}})");
  resourcePaths(fixture,false);
  fixture.index(R"({"OWN_MESH\u0000suffix":{"glb":"test/own.glb"}})");
  resourcePaths(fixture,false);
  fixture.index(R"({"invalid":{"glb":"../escape.glb"},"oWn_MeSh":{"glb":"test/own.glb"}})");
  resourcePaths(fixture,true);
  }

void fractureCache(const Fixture& fixture) {
  const auto root = fixture.root/"fracture";
  std::filesystem::create_directories(root);
  // Eight independent solid tetrahedra exercise the real cache parser and
  // Jolt convex-hull construction, with no geometry from the original game.
  std::ofstream file(root/"OWN_MESH.fracture");
  file << "GOTHIC_FRACTURE 1\npieces 8\n";
  const std::array<std::array<float,3>,4> points = {{{0,0,0},{10,0,0},{0,10,0},{0,0,10}}};
  const std::array<std::array<size_t,3>,4> triangles = {{{0,2,1},{0,1,3},{0,3,2},{1,2,3}}};
  for(size_t piece=0;piece<8;++piece) {
    file << "piece 166.6666666667 " << float(piece)*20.f+2.5f << " 2.5 2.5 4\n";
    for(const auto& triangle:triangles) {
      file << "tri ORIGINAL.TGA\n";
      for(const auto index:triangle) {
        const auto& point = points[index];
        file << point[0]+float(piece)*20.f << ' ' << point[1] << ' ' << point[2] << " 0 1 0 0 0\n";
        }
      }
    }
  file.close();
  require(bool(file),"temporary fracture cache must be writable");

  struct FractureEnvironment {
    std::string previous;
    bool hadPrevious = false;
    explicit FractureEnvironment(const std::filesystem::path& path) {
      if(const auto* old = std::getenv("OPENGOTHIC_FRACTURE")) {
        previous = old;
        hadPrevious = true;
        }
      setenv("OPENGOTHIC_FRACTURE",path.c_str(),1);
      }
    ~FractureEnvironment() {
      if(hadPrevious)
        setenv("OPENGOTHIC_FRACTURE",previous.c_str(),1);
      else
        unsetenv("OPENGOTHIC_FRACTURE");
      }
    } environment(root);
  require(!Resources::meshReplacementLoaded("OWN_MESH.MRM"),"an absent Resources instance has no loaded replacement");
  const auto original = FractureMesh::load("own_mesh.3ds");
  require(original!=nullptr && original->pieces.size()==8 && original->volume>0,
          "synthetic original fracture cache must contain eight valid solid pieces");
  for(const auto& piece:original->pieces)
    require(piece.hull!=nullptr && piece.triangles.size()==4,"each original cache piece must have a real convex hull");

  const auto expectFracture = [&](bool own) {
    Resources resources;
    require(!Resources::meshReplacementLoaded("own_mesh.3ds"),"an index entry alone must not count as a loaded replacement");
    Resources::mountWork(fixture.root/"_work");
    // DynamicWorld::addMovableVob receives an already loaded ProtoMesh before
    // asking for its fracture cache, including when restoring saved destruction.
    const auto* proto = Resources::loadMesh("own_mesh.3ds");
    require(proto!=nullptr && close(proto->bbox()[0].x,own ? -400.f : 10.f),
            "fracture guard must follow the geometry actually selected for the visual");
    require(proto->attach.size()==1 && proto->attach[0].shape!=nullptr &&
            proto->attach[0].sub.size()==1 &&
            proto->attach[0].sub[0].texName==(own ? "OWN_TEST_BARREL_WOOD.TGA" : "ORIGINAL.TGA"),
            "selected visual must retain its collision shape and material");
    for(const auto* visual:{"own_mesh.3ds","OWN_MESH.MRM","path/Own_Mesh.3Ds","path\\own_mesh"}) {
      require(Resources::meshReplacementLoaded(visual)==own,
              "loaded replacement lookup must normalize case, paths and extension");
      const auto fracture = FractureMesh::load(visual);
      if(own) {
        require(fracture==nullptr,"successfully loaded GLB must block the original fracture cache");
        }
      else {
        require(fracture!=nullptr && fracture->pieces.size()==8 && fracture->volume==original->volume,
                "original geometry must retain eight cached pieces for saved destruction");
        for(const auto& piece:fracture->pieces)
          require(piece.hull!=nullptr && piece.triangles.size()==4,
                  "fallback fracture pieces must retain their solid hulls");
        }
      }
    require(!Resources::meshReplacementLoaded("OTHER.MRM"),"loaded replacement state must be specific to the visual");
    bool replaced = !own;
    const auto source = Resources::loadVisualMesh("OWN_MESH.MRM",&replaced);
    require(replaced==own && source.sub_meshes.size()==1 && close(source.bbox.min.x,own ? -400.f : 10.f),
            "shared visual loader must report the geometry selected for the visual");
    require(Resources::meshReplacementLoaded("OWN_MESH.MRM")==own,
            "shared visual loader must retain the actual replacement result");
    checkCensus(fixture,own,2);
    };
  std::filesystem::remove(fixture.root/"meshes/meshes.json");
  expectFracture(false);
  fixture.index("{}");
  expectFracture(false);
  fixture.index();
  setenv("OPENGOTHIC_CONTENT","",1);
  expectFracture(false);
  setenv("OPENGOTHIC_CONTENT",fixture.root.c_str(),1);

  struct FractureLog {
    size_t count = 0;
    FractureLog() {
      Tempest::Log::setOutputCallback([this](Tempest::Log::Mode mode, const char* text) {
        if(mode==Tempest::Log::Info && std::string_view(text).find("fracture: disabled for loaded mesh replacement")!=std::string_view::npos)
          ++count;
        });
      }
    ~FractureLog() { Tempest::Log::setOutputCallback({}); }
    } log;
  glbFile(fixture.root/"meshes/test/own.glb");
  expectFracture(true);
  require(log.count==1,"fracture guard must log once per visual, independent of case and extension");
  expectFracture(true);
  require(log.count==1,"fracture guard must not log again after Resources is recreated");
  textFile(fixture.root/"meshes/test/own.glb","broken glb");
  expectFracture(false);
  std::filesystem::remove(fixture.root/"meshes/test/own.glb");
  expectFracture(false);
  GlbOptions unsupported;
  unsupported.alpha = "BLEND";
  glbFile(fixture.root/"meshes/test/own.glb",unsupported);
  expectFracture(false);
  require(log.count==1,"fallback to the original must not log a disabled fracture cache");
  fixture.indexPath("../test/own.glb");
  expectFracture(false);
  fixture.index();
  glbFile(fixture.root/"meshes/test/own.glb");
  }

void alphaDdsFile(const std::filesystem::path& path, bool transparentBc1, bool dxt5 = false) {
  std::array<uint8_t,144> bytes{};
  auto field = [&](size_t at, uint32_t value) {
    for(size_t i=0;i<4;++i)
      bytes[at*4+i] = uint8_t(value>>(i*8));
    };
  field(0,0x20534444); // DDS
  field(1,124);
  field(2,0x81007);
  field(3,4);
  field(4,4);
  field(5,dxt5 ? 16 : 8);
  field(7,1);
  field(19,32);
  field(20,4);
  field(21,dxt5 ? 0x35545844 : 0x31545844); // DXT5 / DXT1
  field(27,0x1000);
  if(dxt5) {
    bytes[128] = 255; // Opaque alpha endpoint, all alpha indices zero.
    bytes[136] = 255;
    bytes[137] = 255;
    }
  else if(transparentBc1) {
    bytes[130] = 255; // c0=0 <= c1=65535 selects BC1's three-colour mode.
    bytes[131] = 255;
    bytes[132] = 3;   // Pixel 0 uses transparent palette entry 3; pixel 1 uses opaque 0.
    const uint16_t c0 = uint16_t(uint16_t(bytes[128]) | uint16_t(uint16_t(bytes[129])<<8));
    const uint16_t c1 = uint16_t(uint16_t(bytes[130]) | uint16_t(uint16_t(bytes[131])<<8));
    require(c0<=c1 && (bytes[132]&3u)==3u && ((bytes[132]>>2)&3u)==0u,
            "BC1 fixture must contain both transparent and visible texels");
    }
  else {
    bytes[128] = 255; // c0 > c1, opaque four-colour block.
    bytes[129] = 255;
    }
  std::ofstream out(path,std::ios::binary);
  out.write(reinterpret_cast<const char*>(bytes.data()),dxt5 ? 144 : 136);
  if(!out)
    throw std::runtime_error("unable to write mesh alpha DDS fixture");
  }

void maskBc1Policy(const Fixture& fixture) {
  std::filesystem::create_directories(fixture.root/"materials");
  alphaDdsFile(fixture.root/"materials/transparent-bc1.dds",true);
  alphaDdsFile(fixture.root/"materials/opaque-bc1.dds",false);
  alphaDdsFile(fixture.root/"materials/opaque-bc3.dds",false,true);
  textFile(fixture.root/"materials/materials.json",R"({
    "OWN_TEST_BARREL_WOOD.TGA":{"albedo":"transparent-bc1.dds"},
    "ORIGINAL.TGA":{"albedo":"transparent-bc1.dds"},
    "OPAQUE_BC1.TGA":{"albedo":"opaque-bc1.dds"},
    "OPAQUE_BC3.TGA":{"albedo":"opaque-bc3.dds"}
  })");
  GlbOptions options;
  options.alpha = "MASK";
  glbFile(fixture.root/"meshes/test/own.glb",options);
  glbFile(fixture.root/"meshes/test/opaque.glb");
  originalFile(fixture.root/"_work/NATIVE.MRM");
  fixture.index(R"({
    "OWN_MESH":{"glb":"test/own.glb"},
    "OWN_OPAQUE":{"glb":"test/opaque.glb"}
  })");

  Tempest::VulkanApi api;
  Tempest::Device device(api);
  {
    Resources resources(device);
    Shaders shaders;
    shaders.waitCompiler();
    Resources::mountWork(fixture.root/"_work");
    const auto* masked = Resources::loadMesh("OWN_MESH.3DS");
    require(masked!=nullptr && masked->attach.size()==1 && masked->attach[0].sub.size()==1,
            "MASK BC1 fixture must load through the ordinary ProtoMesh path");
    const auto& mask = masked->attach[0].sub[0].material;
    require(mask.tex!=nullptr && mask.tex->format()==Tempest::TextureFormat::DXT1,
            "MASK regression must use an actual uploaded BC1 texture");
    require(mask.alpha==Material::AlphaTest && mask.isTextureInShadowPass(),
            "glTF MASK must retain alpha test and texture sampling in the shadow pass for BC1");

    const auto* opaque = Resources::loadMesh("OWN_OPAQUE.3DS");
    require(opaque!=nullptr && opaque->attach.size()==1 && opaque->attach[0].sub.size()==1,
            "OPAQUE BC1 fixture must load through the ordinary ProtoMesh path");
    const auto& solid = opaque->attach[0].sub[0].material;
    require(solid.tex==mask.tex && solid.alpha==Material::Solid && !solid.isTextureInShadowPass(),
            "glTF OPAQUE must stay solid with the exact same transparent BC1 texture");

    const auto* original = Resources::loadMesh("NATIVE.3DS");
    require(original!=nullptr && original->attach.size()==1 && original->attach[0].sub.size()==1,
            "native MRM BC1 fixture must load through its unchanged path");
    const auto& native = original->attach[0].sub[0].material;
    require(native.tex==mask.tex && native.alpha==Material::Solid,
            "native MRM must preserve its historical BC1 solid classification");

    const auto* bc1 = Resources::loadMaterialAlbedo("OPAQUE_BC1.TGA");
    const auto* bc3 = Resources::loadMaterialAlbedo("OPAQUE_BC3.TGA");
    require(bc1!=nullptr && bc1->format()==Tempest::TextureFormat::DXT1 &&
            bc3!=nullptr && bc3->format()==Tempest::TextureFormat::DXT5,
            "albedo policy transitions must exercise real opaque BC1 and BC3 textures");
    auto changed = mask;
    for(const auto* texture:{bc1,bc3,mask.tex}) {
      changed.setAlbedo(texture);
      require(changed.tex==texture && changed.alpha==Material::AlphaTest && changed.isTextureInShadowPass(),
              "glTF MASK policy must survive albedo changes between BC1 and BC3");
      }
    for(auto mode:{Material::Solid,Material::Water,Material::Ghost,Material::Multiply,Material::Multiply2,
                   Material::Transparent,Material::AdditiveLight}) {
      auto overridden = mask;
      overridden.alpha = mode;
      overridden.setAlbedo(bc1);
      overridden.setAlbedo(bc3);
      require(overridden.alpha==mode,"forced MASK policy must retain explicit material alpha overrides");
      }

    // Both have identical source fields and current alpha after BC3 is selected;
    // only the retained glTF MASK policy differs and must keep buckets distinct.
    const auto source = Resources::loadVisualMesh("OWN_MESH.MRM");
    Material legacyPolicy(source.sub_meshes[0].mat,true);
    auto forcedPolicy = mask;
    legacyPolicy.setAlbedo(bc3);
    forcedPolicy.setAlbedo(bc3);
    require(legacyPolicy.alpha==Material::AlphaTest && forcedPolicy.alpha==Material::AlphaTest,
            "material equality fixture must have the same visible alpha classification");
    require(!(legacyPolicy==forcedPolicy),"material equality must distinguish native and forced MASK policies");
    }
  device.waitIdle();
  }
}

int main() {
  try {
    CommandLine commandLine(0,nullptr);
    Fixture fixture;
    fixture.index();
    resourcePaths(fixture,true);
    resourcePaths(fixture,true,false);
    converterCases(fixture);
    generatedBarrel(fixture);
    rejectionCases(fixture);
    indexCases(fixture);
    fractureCache(fixture);
    setenv("TEMPEST_AUDIO_LOOPBACK","48000",1);
    maskBc1Policy(fixture);
    std::cout << "{\"test\":\"mesh_gltf\",\"passed\":true,\"assertions\":" << assertions << "}\n";
    return 0;
    }
  catch(const std::exception& error) {
    std::cerr << "mesh glTF test failed: " << error.what() << '\n';
    return 1;
    }
  }
