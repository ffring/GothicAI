#include "worldbase.h"

#include "worldchunks.h"

#include <zenkit/Stream.hh>

#include <rapidjson/document.h>
#include <algorithm>
#include <fstream>
#include <chrono>
#include <cctype>
#include <stdexcept>

std::vector<std::byte> readWorldBytes(zenkit::Read& reader) {
  reader.seek(0,zenkit::Whence::END);
  std::vector<std::byte> bytes(reader.tell());
  reader.seek(0,zenkit::Whence::BEG);
  if(reader.read(bytes.data(),bytes.size())!=bytes.size())
    throw std::runtime_error("Unable to read complete ZEN source");
  return bytes;
  }

WorldBaseResult tryWorldBase(asset::WorldMesh& mesh, std::string_view world,
                             const std::vector<std::byte>& source,
                             const std::filesystem::path& dir,
                             const std::filesystem::path& overrideDir) {
  WorldBaseResult result;
  if(dir.empty())
    return result;
  const auto started = std::chrono::steady_clock::now();
  try {
    if(!std::filesystem::exists(dir/"world.json"))
      return result;
    const auto manifest = readWorldChunkManifest(dir);
    const auto separator = world.find_last_of("/\\");
    std::string name(separator==std::string_view::npos ? world : world.substr(separator+1));
    for(auto& c:name)
      c = char(std::toupper(static_cast<unsigned char>(c)));
    if(manifest.source.world!=name)
      throw std::runtime_error("ZEN source world mismatch");
    if(manifest.source.size!=source.size())
      throw std::runtime_error("ZEN source size mismatch");
    if(manifest.source.crc32!=worldChunkCrc32(source.data(),source.size()))
      throw std::runtime_error("ZEN source crc32 mismatch");
    auto base = readWorldChunks(dir,overrideDir);
    result.triangles = base.polygons.material_indices.size();
    result.chunks = manifest.chunks.size();
    mesh.materials = std::move(base.materials);
    mesh.vertices = std::move(base.vertices);
    mesh.features = std::move(base.features);
    mesh.polygons = std::move(base.polygons);
    result.replaced = true;
    }
  catch(const std::exception& e) {
    result.error = e.what();
    }
  result.readMs = std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
  return result;
  }

WorldBaseResult tryWorldData(zenkit::World& world, asset::WorldMesh& worldMesh, std::vector<WorldData::Room>& rooms,
                            std::string_view name, const std::filesystem::path& dir,
                            const std::filesystem::path& overrideDir,
                            const std::vector<std::pair<std::string,uintmax_t>>& mounted) {
  WorldBaseResult result;
  const auto started = std::chrono::steady_clock::now();
  try {
    if(dir.empty())
      throw std::runtime_error("base disabled");
    std::ifstream file(dir/"world.json",std::ios::binary);
    if(!file)
      throw std::runtime_error("world.json missing");
    const std::string text{std::istreambuf_iterator<char>(file),std::istreambuf_iterator<char>()};
    rapidjson::Document table;
    table.Parse<rapidjson::kParseValidateEncodingFlag>(text.data(),text.size());
    if(table.HasParseError() || !table.IsObject() ||
       !table.HasMember("archives") || !table["archives"].IsArray())
      throw std::runtime_error("invalid world archive index");
    std::vector<std::pair<std::string,uintmax_t>> archives;
    for(const auto& archive:table["archives"].GetArray()) {
      if(!archive.IsObject() || !archive.HasMember("name") || !archive["name"].IsString() ||
         !archive.HasMember("size") || !archive["size"].IsUint64())
        throw std::runtime_error("invalid archive identity");
      archives.emplace_back(archive["name"].GetString(),archive["size"].GetUint64());
      }
    std::sort(archives.begin(),archives.end());
    if(archives!=mounted)
      throw std::runtime_error("mounted archives differ from world.json");
    for(const char* required:{"vobs.json","waynet.json","sectors.json","bsp.bin","rooms.bin"})
      if(!std::filesystem::is_regular_file(dir/required))
        throw std::runtime_error(std::string(required)+" missing");
    const auto manifest = readWorldChunkManifest(dir);
    for(const auto& chunk:manifest.chunks)
      if(!std::filesystem::is_regular_file(dir/chunk))
        throw std::runtime_error(chunk+" missing from base");
    const auto separator = name.find_last_of("/\\");
    std::string key(separator==std::string_view::npos ? name : name.substr(separator+1));
    for(auto& c:key)
      c = char(std::toupper(static_cast<unsigned char>(c)));
    if(manifest.source.world!=key)
      throw std::runtime_error("ZEN source world mismatch");
    auto data = WorldData::read(dir,overrideDir);
    if(data.source.world!=manifest.source.world || data.source.size!=manifest.source.size ||
       data.source.crc32!=manifest.source.crc32)
      throw std::runtime_error("world data source differs from world.json");
    if(!data.waynet)
      throw std::runtime_error("world waynet missing");
    auto mesh = readWorldChunks(dir,overrideDir);
    mesh.name = std::move(data.name);
    mesh.bbox = data.bbox;
    result.triangles = mesh.polygons.material_indices.size();
    result.chunks = manifest.chunks.size();
    worldMesh = std::move(mesh);
    world.world_vobs = std::move(data.vobs);
    world.way_net = std::move(data.waynet);
    world.world_bsp_tree = std::move(data.bsp);
    rooms = std::move(data.rooms);
    result.replaced = true;
    }
  catch(const std::exception& error) {
    result.error = error.what();
    }
  result.readMs = std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count();
  return result;
  }
