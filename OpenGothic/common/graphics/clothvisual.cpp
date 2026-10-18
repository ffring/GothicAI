#include "clothvisual.h"

#include <Tempest/Log>
#include <zenkit/MorphMesh.hh>
#include <zenkit/MultiResolutionMesh.hh>
#include <algorithm>
#include <cmath>
#include <map>
#include <sstream>
#include <unordered_map>

#include "clothdata.h"
#include "gothic.h"
#include "graphics/mesh/submesh/packedmesh.h"
#include "physics/cloth.h"
#include "physics/physicmesh.h"
#include "physics/worldwind.h"
#include "utils/fileext.h"
#include "world/world.h"

using namespace Tempest;

namespace {
struct Definition {
  std::string visual, pins, material;
  float stiffness = .8f;
  };

std::string canonical(std::string_view name) {
  const auto slash = name.find_last_of("/\\");
  if(slash!=std::string_view::npos)
    name.remove_prefix(slash+1);
  auto out = std::string(name.substr(0,name.find_last_of('.')));
  for(auto& c:out)
    if(c>='a' && c<='z')
      c = char(c-'a'+'A');
  return out;
  }

bool matches(std::string_view pattern, std::string_view value) {
  size_t p=0, v=0, star=std::string_view::npos, retry=0;
  while(v<value.size()) {
    if(p<pattern.size() && (pattern[p]=='?' || pattern[p]==value[v])) {
      ++p;
      ++v;
      }
    else if(p<pattern.size() && pattern[p]=='*') {
      star = p++;
      retry = v;
      }
    else if(star!=std::string_view::npos) {
      p = star+1;
      v = ++retry;
      }
    else
      return false;
    }
  while(p<pattern.size() && pattern[p]=='*')
    ++p;
  return p==pattern.size();
  }

const Definition* definition(std::string_view visual) {
  static const auto definitions = [] {
    std::vector<Definition> out;
    std::istringstream input(ClothVobData);
    for(std::string line;std::getline(input,line);) {
      if(line.empty() || line[0]=='#')
        continue;
      std::istringstream row(line);
      Definition def;
      std::string stiffness;
      if(!std::getline(row,def.visual,',') || !std::getline(row,def.pins,',') ||
         !std::getline(row,stiffness,','))
        continue;
      std::getline(row,def.material,',');
      try {
        def.stiffness = std::clamp(std::stof(stiffness),0.f,1.f);
        }
      catch(const std::exception&) {
        Log::e("invalid cloth stiffness: ",def.visual);
        continue;
        }
      def.visual = canonical(def.visual);
      if(def.material.empty())
        def.material = "*";
      out.push_back(std::move(def));
      }
    return out;
    }();
  const auto name = canonical(visual);
  for(const auto& def:definitions)
    if(matches(def.visual,name))
      return &def;
  return nullptr;
  }

struct Geometry {
  std::vector<Vec3> positions, normals, physicsPositions;
  std::vector<uint32_t> triangles, physicsTriangles, packedToPhysics, pins;
  const ProtoMesh* fullProto = nullptr;
  std::unique_ptr<ProtoMesh> rigidProto;
  };

std::vector<uint32_t> pinned(const Definition& def, const std::vector<Vec3>& vertices) {
  // Rules select an edge of the cloth material's own bounds in centimetres.
  // Multiple rules (for example both sail corners) are separated by ';'.
  std::vector<uint32_t> out;
  Vec3 lo = vertices.front(), hi = lo;
  const auto component = [](const Vec3& p, size_t axis) {
    return axis==0 ? p.x : axis==1 ? p.y : p.z;
    };
  for(const auto& p:vertices)
    {
    lo = Vec3(std::min(lo.x,p.x),std::min(lo.y,p.y),std::min(lo.z,p.z));
    hi = Vec3(std::max(hi.x,p.x),std::max(hi.y,p.y),std::max(hi.z,p.z));
    }
  std::istringstream rules(def.pins);
  for(std::string rule;std::getline(rules,rule,';');) {
    std::istringstream fields(rule);
    std::string axisText, side, toleranceText;
    if(!std::getline(fields,axisText,':') || !std::getline(fields,side,':') ||
       !std::getline(fields,toleranceText,':'))
      continue;
    const size_t axis = axisText=="x" ? 0 : axisText=="y" ? 1 : axisText=="z" ? 2 : 3;
    if(axis==3 || (side!="min" && side!="max"))
      continue;
    try {
      const float tolerance = std::max(0.f,std::stof(toleranceText));
      const float edge = component(side=="min" ? lo : hi,axis);
      for(uint32_t i=0;i<vertices.size();++i)
        if(std::abs(component(vertices[i],axis)-edge)<=tolerance)
          out.push_back(i);
      }
    catch(const std::exception&) {
      }
    }
  std::sort(out.begin(),out.end());
  out.erase(std::unique(out.begin(),out.end()),out.end());
  return out;
  }

std::shared_ptr<Geometry> loadGeometry(std::string_view visual, const Definition& def) {
  static std::unordered_map<std::string,std::weak_ptr<Geometry>> cache;
  const auto key = std::string(visual);
  if(auto old=cache[key].lock())
    return old;
  std::string resource(visual);
  const bool morph = FileExt::hasExt(resource,"MMS") || FileExt::hasExt(resource,"MMB");
  FileExt::exchangeExt(resource,"3DS","MRM");
  FileExt::exchangeExt(resource,"MMS","MMB");
  auto reader = Resources::getFileBuffer(resource);
  zenkit::MultiResolutionMesh source;
  if(morph) {
    zenkit::MorphMesh mesh;
    mesh.load(reader.get());
    source = std::move(mesh.mesh);
    }
  else
    source.load(reader.get());
  // Use the same deterministic meshlet packing as Resources::loadMesh. A
  // packed vertex can occur in several meshlets, at UV seams and on both sides.
  PackedMesh packed(source,morph ? PackedMesh::PK_VisualMorph : PackedMesh::PK_Visual);
  auto out = std::make_shared<Geometry>();
  for(const auto& v:packed.vertices) {
    out->positions.emplace_back(v.pos[0],v.pos[1],v.pos[2]);
    out->normals.emplace_back(v.norm[0],v.norm[1],v.norm[2]);
    }
  out->packedToPhysics.resize(packed.vertices.size(),uint32_t(-1));
  for(const auto& sub:packed.subMeshes) {
    if(!matches(def.material,sub.material.name))
      continue;
    for(size_t at=sub.iboOffset;at+2<sub.iboOffset+sub.iboLength;at+=3) {
      const uint32_t a = packed.indices[at], b = packed.indices[at+1], c = packed.indices[at+2];
      if(a==b || a==c || b==c)
        continue;
      const auto cross = Vec3::crossProduct(out->positions[b]-out->positions[a],out->positions[c]-out->positions[a]);
      if(cross.quadLength()<1e-6f)
        continue;
      for(const uint32_t id:{a,b,c}) {
        if(out->packedToPhysics[id]==uint32_t(-1)) {
          out->packedToPhysics[id] = uint32_t(out->physicsPositions.size());
          out->physicsPositions.push_back(out->positions[id]);
          }
        out->triangles.push_back(id);
        out->physicsTriangles.push_back(out->packedToPhysics[id]);
        }
      }
    }
  if(out->physicsPositions.empty())
    return nullptr;
  out->pins = pinned(def,out->physicsPositions);
  if(out->pins.empty() || out->pins.size()==out->physicsPositions.size()) {
    Log::e("cloth needs both fixed and free vertices: ",visual);
    return nullptr;
    }
  out->fullProto = Resources::loadMesh(visual);
  // A standard can contain a rigid pole and skull in addition to fabric.
  // Build its rigid collision once per visual, retaining native material flags.
  auto rigid = source;
  rigid.sub_meshes.erase(std::remove_if(rigid.sub_meshes.begin(),rigid.sub_meshes.end(),[&](const auto& sub) {
    return matches(def.material,sub.mat.name);
    }),rigid.sub_meshes.end());
  if(!rigid.sub_meshes.empty()) {
    PackedMesh rigidPacked(rigid,PackedMesh::PK_Visual);
    out->rigidProto = std::make_unique<ProtoMesh>(std::move(rigidPacked),visual);
    }
  cache[key] = out;
  return out;
  }
}

struct ClothVisual::State {
  MeshObjects::Mesh* view = nullptr;
  World* world = nullptr;
  std::shared_ptr<Geometry> geometry;
  std::shared_ptr<Cloth::Instance> physics;
  std::unique_ptr<PhysicMesh> collider;
  bool nativeCollision = false, colliderInitialized = false, colliderClothEnabled = false;
  bool nativeMovable = false;
  Matrix4x4 matrix = Matrix4x4::mkIdentity();
  Matrix4x4 referenceMatrix = Matrix4x4::mkIdentity();
  uint64_t referenceTick = uint64_t(-1);
  zenkit::AnimationType mode = zenkit::AnimationType::NONE;
  float strength = 0;
  std::vector<Vec4> deformation;

  Vec3 reference(const Vec3& local) {
    const auto tick = world->tickCount();
    if(referenceTick!=tick) {
      const float cycle = Gothic::settingsGetF("ENGINE","zWindCycleTime")+
                          Gothic::settingsGetF("ENGINE","zWindCycleTimeVar");
      const auto period = std::isfinite(cycle) && cycle>0.f ? uint64_t(cycle*1000.f) : uint64_t(0);
      auto wind = WorldWind::shaderMatrix(matrix,tick,period,
                                          Gothic::settingsGetI("ENGINE","zWindEnabled")!=0,
                                          mode,strength,world->physic()->cloth().windScale());
      auto inverse = matrix;
      inverse.inverse();
      referenceMatrix = inverse*wind;
      referenceTick = tick;
      }
    auto point = local;
    referenceMatrix.project(point);
    return point;
    }

  void syncCollider() {
    if(!nativeCollision)
      return;
    const bool enabled = world->physic()->cloth().enabled();
    if(colliderInitialized && colliderClothEnabled==enabled)
      return;
    collider.reset();
    colliderInitialized = true;
    colliderClothEnabled = enabled;
    const auto* source = enabled ? geometry->rigidProto.get() : geometry->fullProto;
    if(source!=nullptr) {
      collider = std::make_unique<PhysicMesh>(*source,*world->physic(),nativeMovable);
      collider->setObjMatrix(matrix);
      }
    }

  void update(const std::vector<Vec3>& positions, float weight) {
    syncCollider();
    if(view==nullptr)
      return;
    if(weight<=0.f) {
      view->setDeformation({});
      view->setWind(mode,strength);
      view->setObjMatrix(matrix);
      return;
      }
    deformation.resize(geometry->positions.size()*2);
    for(size_t i=0;i<geometry->positions.size();++i) {
      const auto id = geometry->packedToPhysics[i];
      const Vec3 p = id<positions.size() ? positions[id] : geometry->positions[i];
      // The fourth component also fades the untouched original morph on the GPU.
      deformation[i*2] = Vec4(p.x,p.y,p.z,id<positions.size() ? weight : 0.f);
      deformation[i*2+1] = Vec4();
      }
    for(size_t i=0;i+2<geometry->triangles.size();i+=3) {
      const uint32_t a=geometry->triangles[i], b=geometry->triangles[i+1], c=geometry->triangles[i+2];
      const auto pos = [&](uint32_t id) {
        const auto& p = deformation[id*2];
        return Vec3(p.x,p.y,p.z);
        };
      Vec3 normal = Vec3::crossProduct(pos(b)-pos(a),pos(c)-pos(a));
      // Preserve the source winding, including a separately drawn reverse side.
      const auto rest = Vec3::crossProduct(geometry->positions[b]-geometry->positions[a],geometry->positions[c]-geometry->positions[a]);
      if(Vec3::dotProduct(rest,geometry->normals[a])<0)
        normal = -normal;
      for(const uint32_t id:{a,b,c})
        deformation[id*2+1] += Vec4(normal.x,normal.y,normal.z,0);
      }
    for(size_t i=0;i<geometry->positions.size();++i) {
      const auto& v = deformation[i*2+1];
      Vec3 normal(v.x,v.y,v.z);
      normal = normal.quadLength()>1e-8f ? Vec3::normalize(normal) : geometry->normals[i];
      deformation[i*2+1] = Vec4(normal.x,normal.y,normal.z,0);
      }
    view->setWind(zenkit::AnimationType::NONE,0);
    view->setObjMatrix(matrix);
    view->setDeformation(deformation);
    }
  };

ClothVisual::ClothVisual() = default;
ClothVisual::~ClothVisual() = default;

bool ClothVisual::supports(std::string_view visual) {
  return definition(visual)!=nullptr;
  }

std::unique_ptr<ClothVisual> ClothVisual::create(MeshObjects::Mesh& view, World& world,
                                               const zenkit::VirtualObject& vob, bool enableCollision) {
  const auto* def = definition(vob.visual->name);
  if(def==nullptr || view.isEmpty())
    return nullptr;
  try {
    auto geometry = loadGeometry(vob.visual->name,*def);
    if(geometry==nullptr)
      return nullptr;
    auto out = std::unique_ptr<ClothVisual>(new ClothVisual());
    out->state = std::make_shared<State>();
    auto& state = *out->state;
    state.view = &view;
    state.world = &world;
    state.geometry = std::move(geometry);
    state.nativeCollision = enableCollision;
    state.nativeMovable = vob.visual->type==zenkit::VisualType::MORPH_MESH;
    state.mode = vob.anim_mode;
    state.strength = vob.anim_strength;
    if(state.strength!=0 && state.mode==zenkit::AnimationType::NONE)
      state.mode = zenkit::AnimationType::WIND_ALT;
    if(state.strength==0)
      state.mode = zenkit::AnimationType::NONE;
    state.physics = world.physic()->cloth().add(state.geometry->physicsPositions,state.geometry->physicsTriangles,
                                               state.geometry->pins,state.matrix,def->stiffness,
                                               vob.visual->name,vob.id);
    if(!state.physics)
      return nullptr;
    const auto weak = std::weak_ptr<State>(out->state);
    state.physics->setShaderReference([weak](const Vec3& local, const Vec3&) {
      auto state = weak.lock();
      if(!state)
        return local;
      return state->reference(local);
      });
    state.physics->setUpdateCallback([weak](const std::vector<Vec3>& positions, float weight) {
      if(auto state = weak.lock())
        state->update(positions,weight);
      });
    state.syncCollider();
    return out;
    }
  catch(const std::exception& error) {
    Log::e("cloth mesh unavailable: ",vob.visual->name,": ",error.what());
    return nullptr;
    }
  }

void ClothVisual::setView(MeshObjects::Mesh& view) {
  state->view = &view;
  }

void ClothVisual::setMatrix(const Matrix4x4& matrix) {
  state->matrix = matrix;
  state->referenceTick = uint64_t(-1);
  state->physics->setMatrix(matrix);
  if(state->collider)
    state->collider->setObjMatrix(matrix);
  }
