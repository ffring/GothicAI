#include "asset/fromzenkit.h"
#include "basemesh.h"
#include "commandline.h"
#include "gltfmesh.h"
#include "gothicglb.h"
#include "graphics/mesh/submesh/packedmesh.h"
#include "physics/physicvbo.h"
#include "resources.h"
#include "modeldata.h"

#include <zenkit/Stream.hh>
#include <zenkit/Model.hh>
#include <zenkit/ModelAnimation.hh>
#include <zenkit/ModelScript.hh>
#include <zenkit/MorphMesh.hh>
#include <zenkit/MultiResolutionMesh.hh>
#include <zenkit/Model.hh>
#include <zenkit/ModelScript.hh>
#include <zenkit/Archive.hh>
#include <zenkit/Logger.hh>
#include <zenkit/Texture.hh>
#include <zenkit/addon/texcvt.hh>
#include <Tempest/MemReader>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <algorithm>
#include <bit>
#include <chrono>
#include <cmath>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <set>
#include <stdexcept>
#include <type_traits>

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

  template<class A, class B>
  static void modelField(const A& a, const B& b, const std::string& field) {
    if constexpr(std::is_same_v<A,float> && std::is_same_v<B,float>)
      equal(std::bit_cast<uint32_t>(a),std::bit_cast<uint32_t>(b),field+" bits");
    else if constexpr(std::is_enum_v<A> && std::is_enum_v<B>)
      equal(int64_t(a),int64_t(b),field);
    else
      require(a==b,field+": values differ");
    }

  template<class A, class B>
  static void modelField(const std::vector<A>& a, const std::vector<B>& b, const std::string& field) {
    equal(a.size(),b.size(),field+" count");
    if(a.empty()) return;
    if constexpr(std::is_same_v<A,B> && std::is_trivially_copyable_v<A>)
      if(std::memcmp(a.data(),b.data(),a.size()*sizeof(A))==0) return;
    for(size_t i=0;i<a.size();++i)
      modelField(a[i],b[i],field+"["+std::to_string(i)+"]");
    }

  static void modelField(const zenkit::Vec2& a, const zenkit::Vec2& b, const std::string& field) {
    modelField(a.x,b.x,field+".x"); modelField(a.y,b.y,field+".y");
    }

  static void modelField(const zenkit::Vec3& a, const zenkit::Vec3& b, const std::string& field) {
    modelField(a.x,b.x,field+".x"); modelField(a.y,b.y,field+".y"); modelField(a.z,b.z,field+".z");
    }

  static void modelField(const zenkit::Quat& a, const zenkit::Quat& b, const std::string& field) {
    modelField(a.w,b.w,field+".w"); modelField(a.x,b.x,field+".x");
    modelField(a.y,b.y,field+".y"); modelField(a.z,b.z,field+".z");
    }

  static void modelField(const zenkit::Mat4& a, const zenkit::Mat4& b, const std::string& field) {
    for(unsigned column=0;column<4;++column)
      for(unsigned row=0;row<4;++row)
        modelField(a[column][row],b[column][row],field+"["+std::to_string(column)+"]["+std::to_string(row)+"]");
    }

  static void modelField(const zenkit::Color& a, const zenkit::Color& b, const std::string& field) {
    modelField(a.r,b.r,field+".r"); modelField(a.g,b.g,field+".g");
    modelField(a.b,b.b,field+".b"); modelField(a.a,b.a,field+".a");
    }

  static void modelField(const zenkit::AxisAlignedBoundingBox& a, const zenkit::AxisAlignedBoundingBox& b,
                         const std::string& field) {
    modelField(a.min,b.min,field+".min"); modelField(a.max,b.max,field+".max");
    }

  static void modelField(const zenkit::OrientedBoundingBox& a, const zenkit::OrientedBoundingBox& b,
                         const std::string& field) {
    modelField(a.center,b.center,field+".center"); modelField(a.half_width,b.half_width,field+".half_width");
    for(size_t i=0;i<3;++i) modelField(a.axes[i],b.axes[i],field+".axes["+std::to_string(i)+"]");
    modelField(a.children,b.children,field+".children");
    }

#define MODEL_FIELD(member) modelField(a.member,b.member,field+"." #member)
  static void modelField(const zenkit::Date& a, const asset::ModelDate& b, const std::string& field) {
    MODEL_FIELD(year);
    MODEL_FIELD(month);
    MODEL_FIELD(day);
    MODEL_FIELD(hour);
    MODEL_FIELD(minute);
    MODEL_FIELD(second);
    }

  static void modelField(const zenkit::ModelHierarchyNode& a, const asset::ModelHierarchyNode& b, const std::string& field) {
    MODEL_FIELD(parent_index);
    MODEL_FIELD(name);
    MODEL_FIELD(transform);
    }

  static void modelField(const zenkit::ModelHierarchy& a, const asset::ModelHierarchy& b, const std::string& field) {
    MODEL_FIELD(nodes);
    MODEL_FIELD(bbox);
    MODEL_FIELD(collision_bbox);
    MODEL_FIELD(root_translation);
    MODEL_FIELD(checksum);
    MODEL_FIELD(source_date);
    MODEL_FIELD(source_path);
    }

  static void modelField(const zenkit::AnimationSample& a, const asset::AnimationSample& b, const std::string& field) {
    MODEL_FIELD(position);
    MODEL_FIELD(rotation);
    }

  static void modelField(const zenkit::AnimationEvent& a, const asset::AnimationEvent& b, const std::string& field) {
    MODEL_FIELD(type);
    MODEL_FIELD(frame);
    MODEL_FIELD(tag);
    MODEL_FIELD(probability);
    for(size_t i=0;i<4;++i) {
      modelField(a.content[i],b.content[i],field+".content["+std::to_string(i)+"]");
      modelField(a.values[i],b.values[i],field+".values["+std::to_string(i)+"]");
      }
    }

  static void modelField(const zenkit::ModelAnimation& a, const asset::ModelAnimation& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(next);
    MODEL_FIELD(layer);
    MODEL_FIELD(frame_count);
    MODEL_FIELD(node_count);
    MODEL_FIELD(fps);
    MODEL_FIELD(fps_source);
    MODEL_FIELD(sample_position_min);
    MODEL_FIELD(sample_position_scale);
    MODEL_FIELD(bbox);
    MODEL_FIELD(checksum);
    MODEL_FIELD(source_path);
    MODEL_FIELD(source_date);
    MODEL_FIELD(source_script);
    MODEL_FIELD(samples);
    MODEL_FIELD(events);
    MODEL_FIELD(node_indices);
    }

  static void modelField(const zenkit::MdsSkeleton& a, const asset::MdsSkeleton& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(disable_mesh);
    }

  static void modelField(const zenkit::MdsModelTag& a, const asset::MdsModelTag& b, const std::string& field) {
    MODEL_FIELD(bone);
    }

  static void modelField(const zenkit::MdsEventTag& a, const asset::MdsEventTag& b, const std::string& field) {
    MODEL_FIELD(frame);
    MODEL_FIELD(type);
    MODEL_FIELD(slot);
    MODEL_FIELD(slot2);
    MODEL_FIELD(item);
    MODEL_FIELD(frames);
    MODEL_FIELD(fight_mode);
    MODEL_FIELD(attached);
    }

  static void modelField(const zenkit::MdsParticleEffect& a, const asset::MdsParticleEffect& b, const std::string& field) {
    MODEL_FIELD(frame);
    MODEL_FIELD(index);
    MODEL_FIELD(name);
    MODEL_FIELD(position);
    MODEL_FIELD(attached);
    }

  static void modelField(const zenkit::MdsCameraTremor& a, const asset::MdsCameraTremor& b, const std::string& field) {
    MODEL_FIELD(frame);
    MODEL_FIELD(field1);
    MODEL_FIELD(field2);
    MODEL_FIELD(field3);
    MODEL_FIELD(field4);
    }

  static void modelField(const zenkit::MdsParticleEffectStop& a, const asset::MdsParticleEffectStop& b, const std::string& field) {
    MODEL_FIELD(frame);
    MODEL_FIELD(index);
    }

  static void modelField(const zenkit::MdsSoundEffect& a, const asset::MdsSoundEffect& b, const std::string& field) {
    MODEL_FIELD(frame);
    MODEL_FIELD(name);
    MODEL_FIELD(range);
    MODEL_FIELD(empty_slot);
    }

  static void modelField(const zenkit::MdsSoundEffectGround& a, const asset::MdsSoundEffectGround& b, const std::string& field) {
    MODEL_FIELD(frame);
    MODEL_FIELD(name);
    MODEL_FIELD(range);
    MODEL_FIELD(empty_slot);
    }

  static void modelField(const zenkit::MdsMorphAnimation& a, const asset::MdsMorphAnimation& b, const std::string& field) {
    MODEL_FIELD(frame);
    MODEL_FIELD(animation);
    MODEL_FIELD(node);
    }

  static void modelField(const zenkit::MdsAnimation& a, const asset::MdsAnimation& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(layer);
    MODEL_FIELD(next);
    MODEL_FIELD(blend_in);
    MODEL_FIELD(blend_out);
    MODEL_FIELD(flags);
    MODEL_FIELD(model);
    MODEL_FIELD(direction);
    MODEL_FIELD(first_frame);
    MODEL_FIELD(last_frame);
    MODEL_FIELD(fps);
    MODEL_FIELD(speed);
    MODEL_FIELD(collision_volume_scale);
    MODEL_FIELD(events);
    MODEL_FIELD(pfx);
    MODEL_FIELD(pfx_stop);
    MODEL_FIELD(sfx);
    MODEL_FIELD(sfx_ground);
    MODEL_FIELD(morph);
    MODEL_FIELD(tremors);
    }

  static void modelField(const zenkit::MdsAnimationAlias& a, const asset::MdsAnimationAlias& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(layer);
    MODEL_FIELD(next);
    MODEL_FIELD(blend_in);
    MODEL_FIELD(blend_out);
    MODEL_FIELD(flags);
    MODEL_FIELD(alias);
    MODEL_FIELD(direction);
    }

  static void modelField(const zenkit::MdsAnimationBlend& a, const asset::MdsAnimationBlend& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(next);
    MODEL_FIELD(blend_in);
    MODEL_FIELD(blend_out);
    }

  static void modelField(const zenkit::MdsAnimationCombine& a, const asset::MdsAnimationCombine& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(layer);
    MODEL_FIELD(next);
    MODEL_FIELD(blend_in);
    MODEL_FIELD(blend_out);
    MODEL_FIELD(flags);
    MODEL_FIELD(model);
    MODEL_FIELD(last_frame);
    }

  static void modelField(const zenkit::ModelScript& a, const asset::ModelScript& b, const std::string& field) {
    MODEL_FIELD(skeleton);
    MODEL_FIELD(meshes);
    MODEL_FIELD(disabled_animations);
    MODEL_FIELD(combinations);
    MODEL_FIELD(blends);
    MODEL_FIELD(aliases);
    MODEL_FIELD(model_tags);
    MODEL_FIELD(animations);
    }

  static void modelField(const zenkit::MorphAnimation& a, const asset::MorphAnimation& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(layer);
    MODEL_FIELD(blend_in);
    MODEL_FIELD(blend_out);
    MODEL_FIELD(duration);
    MODEL_FIELD(speed);
    MODEL_FIELD(flags);
    MODEL_FIELD(frame_count);
    MODEL_FIELD(vertices);
    MODEL_FIELD(samples);
    }

  static void modelField(const zenkit::MorphSource& a, const asset::MorphSource& b, const std::string& field) {
    MODEL_FIELD(file_date);
    MODEL_FIELD(file_name);
    }

  static void modelField(const zenkit::MorphMesh& a, const asset::MorphMesh& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(mesh);
    MODEL_FIELD(morph_positions);
    MODEL_FIELD(animations);
    MODEL_FIELD(sources);
    }

  static void modelField(const zenkit::Material& a, const asset::Material& b, const std::string& field) {
    MODEL_FIELD(name);
    MODEL_FIELD(group);
    MODEL_FIELD(color);
    MODEL_FIELD(smooth_angle);
    MODEL_FIELD(texture);
    MODEL_FIELD(texture_scale);
    MODEL_FIELD(texture_anim_fps);
    MODEL_FIELD(texture_anim_map_mode);
    MODEL_FIELD(texture_anim_map_dir);
    MODEL_FIELD(disable_collision);
    MODEL_FIELD(disable_lightmap);
    MODEL_FIELD(dont_collapse);
    MODEL_FIELD(detail_object);
    MODEL_FIELD(detail_object_scale);
    MODEL_FIELD(force_occluder);
    MODEL_FIELD(environment_mapping);
    MODEL_FIELD(environment_mapping_strength);
    MODEL_FIELD(wave_mode);
    MODEL_FIELD(wave_speed);
    MODEL_FIELD(wave_max_amplitude);
    MODEL_FIELD(wave_grid_size);
    MODEL_FIELD(ignore_sun);
    MODEL_FIELD(alpha_func);
    MODEL_FIELD(default_mapping);
    }

  static void modelField(const zenkit::MeshTriangle& a, const asset::MeshTriangle& b, const std::string& field) {
    for(size_t i=0;i<3;++i) modelField(a.wedges[i],b.wedges[i],field+".wedges["+std::to_string(i)+"]");
    }

  static void modelField(const zenkit::MeshWedge& a, const asset::MeshWedge& b, const std::string& field) {
    MODEL_FIELD(normal);
    MODEL_FIELD(texture);
    MODEL_FIELD(index);
    }

  static void modelField(const zenkit::SubMesh& a, const asset::SubMesh& b, const std::string& field) {
    MODEL_FIELD(mat);
    MODEL_FIELD(triangles);
    MODEL_FIELD(wedges);
    }

  static void modelField(const zenkit::MultiResolutionMesh& a, const asset::StaticMesh& b, const std::string& field) {
    MODEL_FIELD(positions);
    MODEL_FIELD(normals);
    MODEL_FIELD(sub_meshes);
    MODEL_FIELD(materials);
    MODEL_FIELD(alpha_test);
    MODEL_FIELD(bbox);
    MODEL_FIELD(obbox);
    }

  static void modelField(const zenkit::SoftSkinWeightEntry& a, const asset::SkinWeight& b, const std::string& field) {
    MODEL_FIELD(weight);
    MODEL_FIELD(position);
    MODEL_FIELD(node_index);
    }

  static void modelField(const zenkit::SoftSkinMesh& a, const asset::SoftSkinMesh& b, const std::string& field) {
    MODEL_FIELD(mesh);
    MODEL_FIELD(bboxes);
    MODEL_FIELD(weights);
    }

  static void modelField(const zenkit::ModelMesh& a, const asset::ModelMesh& b, const std::string& field) {
    MODEL_FIELD(meshes);
    equal(a.attachments.size(),b.attachments.size(),field+".attachments count");
    size_t i=0;
    for(const auto& [name,mesh]:a.attachments) {
      const auto where=field+".attachments["+std::to_string(i)+"]";
      require(name==b.attachments[i].first,where+": native source attachment order differs");
      modelField(mesh,b.attachments[i].second,where+"."+name);
      ++i;
      }
    }

  static void modelField(const zenkit::Model& a, const asset::Model& b, const std::string& field) {
    MODEL_FIELD(hierarchy);
    MODEL_FIELD(mesh);
    }

#undef MODEL_FIELD

  static void modelField(const std::vector<zenkit::AnimationSample>& a,
                         const std::vector<asset::AnimationSample>& b, const std::string& field) {
    equal(a.size(),b.size(),field+" count");
    static_assert(sizeof(zenkit::AnimationSample)==sizeof(asset::AnimationSample));
    if(!a.empty() && std::memcmp(a.data(),b.data(),a.size()*sizeof(a[0]))!=0)
      for(size_t i=0;i<a.size();++i) modelField(a[i],b[i],field+"["+std::to_string(i)+"]");
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

  static void modelNames(const zenkit::VfsNode& directory, std::set<std::string>& names) {
    for(const auto& node:directory.children()) {
      if(node.type()==zenkit::VfsNodeType::DIRECTORY) {
        modelNames(node,names);
        continue;
        }
      auto name=node.name();
      for(auto& c:name) c=char(std::toupper(static_cast<unsigned char>(c)));
      if(name.ends_with(".MDH") || name.ends_with(".MDM") || name.ends_with(".MDL") ||
         name.ends_with(".MAN") || name.ends_with(".MMB") || name.ends_with(".MSB") || name.ends_with(".MDS"))
        names.insert(std::move(name));
      }
    }

  static size_t models(const fs::path& base) {
    Resources::mountWork(CommandLine::inst().nestedPath({u"_work"},Tempest::Dir::FT_Dir));
    const auto index=json(base/"models/models.json");
    require(index.HasMember("format") && index["format"].IsString() &&
            std::string_view(index["format"].GetString())=="gothic-models" && index["version"]==1,
            "model index must use gothic-models version 1");
    require(index.HasMember("files") && index["files"].IsObject(),"model index must contain file records");
    const auto& records=index["files"];
    std::set<std::string> names;
    modelNames(Resources::vdfsIndex().root(),names);
    equal(names.size(),size_t(records.MemberCount()),"model index must include every mounted model resource");
    std::map<std::string,size_t> counts;
    for(const auto& name:names) {
      require(records.HasMember(name.c_str()),name+": source model missing from base index");
      require(records[name.c_str()].IsString(),name+": model record path must be a string");
      const auto* entry=Resources::vdfsIndex().find(name);
      require(entry!=nullptr,name+": original model resource missing");
      auto reader=entry->open_read();
      const auto extension=fs::path(name).extension().string();
      if(extension==".MDH") {
        zenkit::ModelHierarchy source{};
        source.load(reader.get());
        asset::ModelHierarchy loaded;
        require(Resources::loadHierarchyData(name,loaded),name+": hierarchy must load through Resources");
        modelField(source,loaded,name);
        }
      else if(extension==".MDM") {
        zenkit::ModelMesh source{};
        source.load(reader.get());
        asset::ModelMesh loaded;
        require(Resources::loadModelMeshData(name,loaded),name+": model mesh must load through Resources");
        modelField(source,loaded,name);
        }
      else if(extension==".MDL") {
        zenkit::Model source{};
        source.load(reader.get());
        asset::Model loaded;
        require(Resources::loadModelData(name,loaded),name+": combined model must load through Resources");
        modelField(source,loaded,name);
        }
      else if(extension==".MAN") {
        zenkit::ModelAnimation source{};
        source.load(reader.get());
        asset::ModelAnimation loaded;
        require(Resources::loadAnimationData(name,loaded),name+": animation must load through Resources");
        modelField(source,loaded,name);
        }
      else if(extension==".MMB") {
        zenkit::MorphMesh source{};
        source.load(reader.get());
        modelField(source,Resources::loadMorphData(name),name);
        }
      else {
        zenkit::ModelScript source{};
        source.load(reader.get());
        asset::ModelScript loaded;
        require(Resources::loadModelScriptData(name,loaded),name+": model script must load through Resources");
        modelField(source,loaded,name);
        }
      ++counts[extension];
      }
    std::printf("Models: %zu equal to independently parsed sources",names.size());
    for(const auto& [extension,count]:counts) std::printf("; %s=%zu",extension.c_str(),count);
    std::puts("; selected through Resources; every owned field and native attachment order equal");
    return names.size();
    }

  static void modelFixtures(const fs::path& temporary) {
    zenkit::ModelHierarchy hierarchy{};
    hierarchy.nodes={{-1,"ROOT",zenkit::Mat4::identity()},{0,"CHILD",zenkit::Mat4::identity()}};
    hierarchy.nodes[1].transform[3]={-0.f,12.25f,-34.5f,1};
    hierarchy.bbox={{-10,-20,-30},{40,50,60}};
    hierarchy.collision_bbox={{-1,-2,-3},{4,5,6}};
    hierarchy.root_translation={-0.f,17.5f,-22.25f}; hierarchy.checksum=0x12345678;
    hierarchy.source_date={2026,10,25,12,34,56}; hierarchy.source_path="source/model.asc";
    auto file=temporary/"MODEL_CONTRACT.MDH.json";
    ModelData::writeHierarchy(asset::fromZenKit(hierarchy),file);
    modelField(hierarchy,ModelData::readHierarchy(file),"synthetic hierarchy");

    zenkit::ModelAnimation animation{};
    animation.name="ANIMATION"; animation.next="NEXT"; animation.layer=3;
    animation.frame_count=2; animation.node_count=1; animation.fps=25.f; animation.fps_source=30.f;
    animation.sample_position_min=-0.f; animation.sample_position_scale=.03125f;
    animation.bbox=hierarchy.bbox; animation.checksum=hierarchy.checksum;
    animation.source_path="source/animation.asc"; animation.source_date=hierarchy.source_date;
    animation.source_script="ani (ANIMATION)"; animation.node_indices={1};
    animation.samples={{{-0.f,3,4},{.8f,-0.f,.6f,0}},{{5,-6,7},{-.8f,0,-.6f,-0.f}}};
    zenkit::AnimationEvent event{};
    event.type=zenkit::AnimationEventType::CAMERA_TREMOR; event.frame=7; event.tag="EVENT";
    for(size_t i=0;i<4;++i) { event.content[i]="content"+std::to_string(i); event.values[i]=float(i)-.5f; }
    event.values[0]=-0.f; event.probability=.75f; animation.events.push_back(event);
    file=temporary/"MODEL_CONTRACT.MAN.json";
    ModelData::writeAnimation(asset::fromZenKit(animation),file);
    modelField(animation,ModelData::readAnimation(file),"synthetic animation");

    zenkit::ModelScript script{};
    script.skeleton={"SOURCE.ASC",true}; script.meshes={"BODY.ASC","ARMOR.ASC"};
    script.disabled_animations={"DISABLED"}; script.model_tags={{"BONE"}};
    script.combinations={{"COMB",2,"NEXT",.25f,.5f,zenkit::AnimationFlags(0x81),"MODEL",5}};
    script.blends={{"BLEND","NEXT",.125f,.375f}};
    script.aliases={{"ALIAS",4,"NEXT",.25f,.5f,zenkit::AnimationFlags::MOVE,"ANIMATION",zenkit::AnimationDirection::BACKWARD}};
    zenkit::MdsAnimation ani{};
    ani.name="ANIMATION"; ani.layer=1; ani.next="NEXT"; ani.blend_in=.25f; ani.blend_out=.75f;
    ani.flags=zenkit::AnimationFlags::ROTATE; ani.model="SOURCE.ASC";
    ani.direction=zenkit::AnimationDirection::BACKWARD; ani.first_frame=3; ani.last_frame=7;
    ani.fps=25.f; ani.speed=1.5f; ani.collision_volume_scale=.875f;
    ani.events={{7,zenkit::MdsEventType::ITEM_EXCHANGE,"SLOT","SLOT2","ITEM",{1,3,5},zenkit::MdsFightMode::INVALID,true}};
    ani.pfx={{2,3,"PARTICLE","BONE",true}}; ani.pfx_stop={{5,3}};
    ani.sfx={{3,"SOUND",321.f,true}}; ani.sfx_ground={{4,"GROUND",456.f,true}};
    ani.morph={{6,"MORPH","HEAD"}}; ani.tremors={{8,1,2,3,4}};
    script.animations.push_back(ani);
    file=temporary/"MODEL_CONTRACT.MSB.json";
    ModelData::writeScript(asset::fromZenKit(script),file);
    modelField(script,ModelData::readScript(file),"synthetic script");

    zenkit::MorphMesh morph{};
    morph.name="MORPH"; morph.mesh.bbox=hierarchy.bbox;
    morph.mesh.positions={{1,2,3},{-0.f,4,5}}; morph.mesh.normals={{0,1,0},{0,0,1}};
    morph.morph_positions={{3,2,1},{6,5,4}};
    morph.animations={{"MOVE",2,.25f,.5f,1.5f,2.5f,0x81,2,{1},{{-0.f,.5f,.25f},{1,2,3}}}};
    morph.sources={{hierarchy.source_date,"source/morph.asc"}};
    file=temporary/"MODEL_CONTRACT.MMB.json";
    ModelData::writeMorph(asset::fromZenKit(morph),file);
    modelField(morph,ModelData::readMorph(file),"synthetic morph");

    zenkit::Model model{};
    model.hierarchy=hierarchy;
    std::vector<std::string> order;
    for(size_t i=0;i<24;++i) {
      const auto name="ATTACHMENT_"+std::to_string(i);
      order.push_back(name);
      model.mesh.attachments.emplace(name,morph.mesh);
      }
    zenkit::SoftSkinMesh skin{};
    skin.mesh=morph.mesh;
    skin.weights={{{.25f,{1,-0.f,3},0},{.75f,{-1,2,4},1}},{{1.f,{5,6,-7},1}}};
    zenkit::OrientedBoundingBox box{};
    box.center={1,2,3}; box.axes[0]={1,0,0}; box.axes[1]={0,1,0}; box.axes[2]={0,0,1}; box.half_width={4,5,6};
    auto child=box; child.center={-1,-2,-3}; box.children.push_back(child);
    skin.bboxes={box}; skin.mesh.obbox=box;
    zenkit::Material material{};
    material.name="MATERIAL"; material.group=zenkit::MaterialGroup::WOOD; material.color={12,34,56,78};
    material.smooth_angle=33.f; material.texture="TEXTURE.TGA"; material.texture_scale={2,3};
    material.texture_anim_fps=7.f; material.texture_anim_map_mode=zenkit::AnimationMapping::LINEAR;
    material.texture_anim_map_dir={-.25f,.5f}; material.disable_collision=true; material.disable_lightmap=true;
    material.dont_collapse=true; material.detail_object="DETAIL"; material.detail_object_scale=.75f;
    material.force_occluder=true; material.environment_mapping=true; material.environment_mapping_strength=.625f;
    material.wave_mode=zenkit::WaveMode::WIND; material.wave_speed=zenkit::WaveSpeed::FAST;
    material.wave_max_amplitude=9.f; material.wave_grid_size=13.f; material.ignore_sun=true;
    material.alpha_func=zenkit::AlphaFunction::BLEND; material.default_mapping={5,6};
    skin.mesh.materials={material}; skin.mesh.alpha_test=0;
    zenkit::SubMesh sub{}; sub.mat=material; sub.triangles={{{0,1,0}}};
    sub.wedges={{{0,1,0},{.25f,.75f},0},{{0,0,1},{.5f,.125f},1}};
    skin.mesh.sub_meshes={sub}; model.mesh.meshes.push_back(skin);
    auto stored=asset::fromZenKit(model);
    // Simulate a foreign standard library's map order while retaining source insertion order.
    std::reverse(stored.mesh.attachments.begin(),stored.mesh.attachments.end());
    file=temporary/"MODEL_CONTRACT.MDM.json";
    ModelData::writeMesh(stored.mesh,file,order);
    modelField(model.mesh,ModelData::readMesh(file),"synthetic model mesh");
    file=temporary/"MODEL_CONTRACT.MDL.json";
    ModelData::writeModel(stored,file,order);
    modelField(model,ModelData::readModel(file),"synthetic combined model");
    std::puts("Model fixtures: complete script/MAN events, metadata, sample bits, skin weights, nested OBB and native attachment order equal");
    }

  static void census(const fs::path& file, size_t meshes, size_t models) {
    const auto data=json(file);
    equal(data["replacements"].Size(),meshes+models,"census base mesh/model count");
    size_t selectedMeshes=0,selectedModels=0,sourceModels=0;
    for(const auto& row:data["replacements"].GetArray()) {
      const std::string_view kind=row["kind"].GetString(),path=row["replacement"].GetString();
      if(kind=="basemodel") {
        ++selectedModels;
        require(path.starts_with("base/models/"),"base model census path");
        equal(row["opens"].GetUint64(),1u,"base model selected once");
        }
      else {
        ++selectedMeshes;
        require(kind=="basemesh","base mesh census kind");
        require(path.starts_with("base/meshes/"),"base mesh census path");
        }
      }
    equal(selectedMeshes,meshes,"census selected mesh count");
    equal(selectedModels,models,"census selected model count");
    for(const auto& row:data["files"].GetArray()) {
      const std::string_view kind=row["kind"].GetString();
      if(kind!="model" && kind!="skeleton" && kind!="anim" && kind!="morph") continue;
      ++sourceModels;
      equal(row["opens"].GetUint64(),1u,std::string(row["name"].GetString())+": only independent source oracle may open the archive");
      }
    equal(sourceModels,models,"census independent source model count");
    std::puts("Model census: each source opened once by the oracle; Resources selected each base record without original opens");
    }

  static Resources::TextureData decodeArchive(zenkit::Read& input, bool compiled, bool forceMips) {
    if(compiled) {
      zenkit::Texture tex;
      tex.load(&input);
      if(tex.format()>=zenkit::TextureFormat::DXT1 && tex.format()<=zenkit::TextureFormat::DXT5) {
        const auto dds=zenkit::to_dds(tex);
        Tempest::MemReader reader(reinterpret_cast<const uint8_t*>(dds.data()),dds.size());
        Resources::TextureData out{Tempest::Pixmap(reader)};
        out.useMipmap=forceMips || out.pixmap.mipCount()>1;
        return out;
        }
      const auto rgba=tex.as_rgba8(0);
      Resources::TextureData out{Tempest::Pixmap(tex.width(),tex.height(),Tempest::TextureFormat::RGBA8),true};
      std::memcpy(out.pixmap.data(),rgba.data(),rgba.size());
      return out;
      }
    input.seek(0,zenkit::Whence::END);
    std::vector<uint8_t> bytes(input.tell());
    input.seek(0,zenkit::Whence::BEG);
    require(input.read(bytes.data(),bytes.size())==bytes.size(),"incomplete archive texture");
    Tempest::MemReader reader(bytes.data(),bytes.size());
    Resources::TextureData out{Tempest::Pixmap(reader)};
    out.useMipmap=forceMips || out.pixmap.mipCount()>1;
    return out;
    }

  static void legacyModelBounds() {
    for(const auto* name:{"ARMOR_BDT_H.MDL","FIREPLACE_HIGH2.MDM"}) {
      auto file=Resources::getFileBuffer(name);
      file->seek(0,zenkit::Whence::END);
      std::vector<std::byte> source(file->tell());
      file->seek(0,zenkit::Whence::BEG);
      require(file->read(source.data(),source.size())==source.size(),std::string(name)+": source read");
      auto input=zenkit::Read::from(&source);
      const bool model=std::string_view(name).ends_with(".MDL");
      if(model) {
        zenkit::ModelHierarchy hierarchy {};
        hierarchy.load(input.get());
        }
      // Reach the first attachment or soft-skin proto using only container
      // headers. Bounds below are read independently of the MRM decoder.
      size_t payload=0,size=0;
      while(!input->eof()) {
        const auto type=input->read_ushort();
        size=input->read_uint();
        payload=input->tell();
        if(type==0xB100) break;
        input->seek(static_cast<std::ptrdiff_t>(payload+size),zenkit::Whence::BEG);
        }
      require(payload>0 && payload+size<=source.size(),std::string(name)+": proto chunk");
      auto metadata=zenkit::Read::from(source.data()+payload,size);
      equal(metadata->read_ushort(),model ? 0x805u : 0x705u,std::string(name)+" proto version");
      const auto contentSize=metadata->read_uint();
      metadata->seek(contentSize,zenkit::Whence::CUR);
      const auto submeshes=metadata->read_ubyte();
      metadata->seek(16+80*submeshes,zenkit::Whence::CUR);
      auto archive=zenkit::ReadArchive::from(metadata.get());
      for(unsigned i=0;i<submeshes;++i) {
        (void) archive->read_string();
        zenkit::ArchiveObject material {};
        require(archive->read_object_begin(material) && material.class_name=="zCMaterial",
                std::string(name)+": material object");
        archive->skip_object(true);
        }
      const auto alpha=metadata->read_ubyte();
      require(alpha<=1,std::string(name)+": source alpha-test byte");
      zenkit::AxisAlignedBoundingBox expected {};
      expected.load(metadata.get());
      zenkit::OrientedBoundingBox expectedObb;
      expectedObb.load(metadata.get());
      metadata->seek(16,zenkit::Whence::CUR);
      equal(metadata->tell(),size,std::string(name)+" source proto end");

      const auto check=[&](const zenkit::MultiResolutionMesh& mesh) {
        equal(mesh.alpha_test,alpha,std::string(name)+" alpha test");
        for(unsigned axis=0;axis<3;++axis) {
          equal(std::bit_cast<uint32_t>(mesh.bbox.min[axis]),std::bit_cast<uint32_t>(expected.min[axis]),
                std::string(name)+" bbox min bits");
          equal(std::bit_cast<uint32_t>(mesh.bbox.max[axis]),std::bit_cast<uint32_t>(expected.max[axis]),
                std::string(name)+" bbox max bits");
          require(std::isfinite(mesh.bbox.min[axis]) && std::isfinite(mesh.bbox.max[axis]) &&
                  mesh.bbox.min[axis]<=mesh.bbox.max[axis] && std::abs(mesh.bbox.min[axis])<1000.f &&
                  std::abs(mesh.bbox.max[axis])<1000.f,std::string(name)+": finite model-scale bounds");
          }
        const auto nodes=[&](auto&& self,const zenkit::OrientedBoundingBox& box)->size_t {
          size_t count=1;
          for(const auto& child:box.children) count+=self(self,child);
          require(count<=13,std::string(name)+": bounded OBB tree");
          return count;
          };
        equal(nodes(nodes,mesh.obbox),nodes(nodes,expectedObb),std::string(name)+" OBB nodes");
        };
      // Bound the first regression read to its own proto. On the old parser
      // this fails before a full model can allocate a spurious OBB tree at EOF.
      zenkit::MultiResolutionMesh proto {};
      auto bounded=zenkit::Read::from(source.data()+payload,size);
      proto.load_from_section(bounded.get());
      check(proto);
      equal(bounded->tell(),size,std::string(name)+" decoded proto end");

      std::vector<std::string> errors;
      zenkit::Logger::set(zenkit::LogLevel::ERROR,[&](auto,const char* component,const char* message) {
        errors.emplace_back(std::string(component)+": "+message);
        });
      auto original=zenkit::Read::from(&source);
      if(model) {
        zenkit::Model parsed {};
        parsed.load(original.get());
        equal(parsed.mesh.meshes.size(),2u,"legacy armor skin count");
        check(parsed.mesh.meshes.front().mesh);
        }
      else {
        zenkit::ModelMesh parsed {};
        parsed.load(original.get());
        equal(parsed.attachments.size(),1u,"legacy fireplace attachment count");
        check(parsed.attachments.begin()->second);
        }
      zenkit::Logger::set_default(zenkit::LogLevel::INFO);
      require(errors.empty(),std::string(name)+": "+(errors.empty() ? "" : errors.front()));
      std::printf("Legacy proto: %s, alpha=%u, bounds and OBB match source bytes; no parser errors\n",name,unsigned(alpha));
      }
    }

  static void modelTagBoundary() {
    auto input=Resources::getFileBuffer("BLATTCRAWLER.MSB");
    size_t end=0;
    while(!input->eof()) {
      const auto type=input->read_ushort();
      const auto size=input->read_uint();
      end=input->tell()+size;
      if(type==0xF590) break;
      input->seek(static_cast<std::ptrdiff_t>(end),zenkit::Whence::BEG);
      }
    require(!input->eof() && input->read_line(false)=="DEF_HIT_LIMB","model tag source event");
    const auto bone=input->read_line(false);
    require(bone=="BIP01 L FRONT CLAW","model tag source bone");
    input->seek(static_cast<std::ptrdiff_t>(end),zenkit::Whence::BEG);
    equal(input->read_ushort(),0xF520u,"animation after model tag");
    (void) input->read_uint();
    const auto following=input->read_line(false);
    require(!following.empty(),"following animation source name");

    std::vector<std::string> errors;
    zenkit::Logger::set(zenkit::LogLevel::ERROR,[&](auto,const char* component,const char* message) {
      errors.emplace_back(std::string(component)+": "+message);
      });
    input->seek(0,zenkit::Whence::BEG);
    zenkit::ModelScript script {};
    script.load(input.get());
    zenkit::Logger::set_default(zenkit::LogLevel::INFO);
    require(errors.empty(),"model tag boundary: "+(errors.empty() ? "" : errors.front()));
    equal(script.model_tags.size(),1u,"model tag count");
    require(script.model_tags.front().bone==bone,"model tag must retain its source bone");
    require(!script.animations.empty() && script.animations.front().name==following,
            "model tag must retain the following animation");
    std::puts("Model tag: BLATTCRAWLER.MSB bone and following animation match source; no parser errors");
    }

  static void run(const fs::path& base, const fs::path& temporary) {
    setenv("OPENGOTHIC_CONTENT","",1);
    setenv("OPENGOTHIC_TEXTURES","",1);
    const auto censusPath=temporary/"census.json";
    setenv("OPENGOTHIC_CENSUS",censusPath.c_str(),1);
    std::string meshKey;
    size_t textureCount=0,meshCount=0,modelCount=0,dds=0,png=0,tga=0,vertices=0,indices=0,meshlets=0;
    {
    Resources resources;
    require(!resources.baseTextures.enabled && !resources.baseMeshes.enabled && !resources.baseModels.enabled,
            "base must wait for mounted archives");
    Resources::loadVdfs({},true);
    require(resources.baseTextures.enabled && resources.baseMeshes.enabled && resources.baseModels.enabled,
            "matching archives must enable texture, mesh and model indices");
    require(!resources.baseTextures.files.empty() && !resources.baseMeshes.files.empty() && !resources.baseModels.files.empty(),
            "texture, mesh and model indices must contain files");
    modelCount=models(base);
    modelFixtures(temporary);
    for(const auto& [key,file]:resources.baseTextures.files) {
      auto* entry=Resources::vdfsIndex().find(key+"-C.TEX");
      const bool compiled=entry!=nullptr;
      if(!compiled)
        entry=Resources::vdfsIndex().find(key+".TGA");
      require(entry!=nullptr,key+": source texture missing");
      for(const bool forceMips:{false,true}) {
        auto input=entry->open_read();
        const auto original=decodeArchive(*input,compiled,forceMips);
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
    census(censusPath,meshCount,modelCount);
    // Keep parser regression reads separate from the runtime-open census.
    {
    Resources resources;
    Resources::loadVdfs({},true);
    legacyModelBounds();
    modelTagBoundary();
    }
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
    bool rejected=false;
    try {
      Resources resources;
      Resources::loadVdfs({},true);
      }
    catch(const BaseStore::Error& error) { rejected=std::string(error.what()).find("OPENGOTHIC_BASE")!=std::string::npos; }
    require(rejected,"invalid explicit base must fail with recovery instructions");

    const auto incomplete=temporary/"missing-model-index";
    fs::create_directories(incomplete/"models");
    fs::copy_file(base/"base.json",incomplete/"base.json");
    for(const auto* kind:{"textures","meshes","zen"})
      fs::create_directory_symlink(base/kind,incomplete/kind);
    setenv("OPENGOTHIC_BASE",incomplete.c_str(),1);
    std::string missingModelIndexError;
    try {
      Resources resources;
      Resources::loadVdfs({},true);
      }
    catch(const BaseStore::Error& error) { missingModelIndexError=error.what(); }
    require(missingModelIndexError.find("base index missing: "+(incomplete/"models/models.json").string())!=std::string::npos &&
            missingModelIndexError.find("Unset OPENGOTHIC_BASE to import automatically")!=std::string::npos,
            "base without models/models.json must fail with recovery instructions");
    setenv("OPENGOTHIC_BASE",base.c_str(),1);
    {
    Resources resources;
    Resources::loadVdfs({},true);
    require(Resources::loadVisualMesh("BASE_CONTRACT_MISSING.MRM").positions.empty(),"missing mesh must stay empty");
    require(Resources::loadVobBundle("BASE_CONTRACT_MISSING.ZEN")->empty(),"missing bundle must stay empty");
    }
    const auto missing=json(censusPath);
    equal(missing["files"].Size(),0u,"missing resources must not open archives");
    equal(missing["base_misses"].Size(),2u,"missing resources must enter the census");

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
    std::puts("Selection: invalid base rejected; missing resources recorded; own content wins; census kinds stay separate");
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
