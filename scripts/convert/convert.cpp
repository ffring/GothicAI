// Converts Gothic resources into our own formats (docs/asset-migration.md).
//
//   content-convert world <gothic dir> <WORLD.ZEN> <out dir>
//       Write chunks/<x>_<z>.glb, world.json and non-mesh world data;
//       read everything back and compare with the source.
//   content-convert verify-world <gothic dir> <WORLD.ZEN> <world dir> [override dir]
//       Compare existing chunks and world data with the source .ZEN.
//   content-convert zen|verify-zen <gothic dir> <dir>
//       Engine-consumed meshless .ZEN files, including loose _work files.
//   content-convert textures <gothic dir> <out dir>
//       Every compiled texture (*-C.TEX) as <NAME>.dds (DXT blocks and mips
//       copied as the engine builds them) or <NAME>.png (other formats, top
//       level, as the engine uploads them); raw .TGA files copied as they are;
//       textures.json lists them with the archives they came from.
//   content-convert verify-textures <gothic dir> <dir>
//   content-convert meshes|verify-meshes <gothic dir> <dir>
//       Every static mesh (*.MRM) as <NAME>.glb (common/basemesh.h);
//       meshes.json lists them with the source archives.
//   content-convert models|verify-models <gothic dir> <dir>
//       Model, hierarchy, script, skeletal and morph animation records as
//       lossless JSON + binary arrays, including loose _work sources.
//   content-convert fonts|verify-fonts <gothic dir> <dir>
//       Every selected FNT as a lossless JSON + binary record (common/fontdata.h),
//       chosen through the engine's runtime VFS; fonts.json indexes them.
//   content-convert programs|verify-programs <gothic dir> <dir>
//       Every Daedalus program the engine VFS, the _compiled directories or the
//       server can select, as field-by-field records (common/programdata.h);
//       programs.json maps the original choices to them.
//   content-convert sources|verify-sources <gothic dir> <dir>
//       sources.json: selected FNT/audio/BIK/music/DAT/OU inputs of the engine
//       and server readers, their origin and SHA-256; no payload conversion.
//   content-convert --self-test
//       Round trips of synthetic meshes, world data and presets; no game files.
//
// The output is Gothic content: it never goes into git.

#include "baseimport.h"
#include "sha256.h"
#include "asset/fromzenkit.h"
#include "basemesh.h"
#include "fontdata.h"
#include "programdata.h"
#include "worldchunks.h"
#include "worlddata.h"
#include "gothicglb.h"

#include <zenkit/Font.hh>
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
#include <bit>
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
  if(!ok) throw std::runtime_error(what);
  }

asset::Material material(const std::string& name, const std::string& texture, asset::MaterialGroup group) {
  asset::Material m;
  m.name    = name;
  m.texture = texture;
  m.group   = group;
  m.color   = {200,180,160,255};
  m.texture_scale = {0.25f,0.125f};
  m.alpha_func = asset::AlphaFunction::NONE;
  return m;
  }

int selfTest() {
  // NIST SHA-256 vectors: empty, one block, two padding blocks and a long message.
  const std::pair<std::string,std::string_view> vectors[] = {
    {"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    {"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
    {"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"},
    {std::string(1000000,'a'), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"}
    };
  for(const auto& [input,expected]:vectors) {
    require(BaseImport::sha256(input)==expected,"SHA-256 NIST vector");
    Sha256 streamed;
    // Exercise the small, unaligned updates used by the asset digest.
    for(size_t i=0;i<input.size();i+=7)
      streamed.update(input.data()+i,std::min(size_t(7),input.size()-i));
    require(streamed.finish()==expected,"SHA-256 streamed NIST vector");
    }

  asset::WorldMesh mesh;
  mesh.materials.push_back(material("UNUSED","UNUSED.TGA",asset::MaterialGroup::STONE));
  // Sector name with a byte above 0x7F: must survive byte for byte.
  mesh.materials.push_back(material("C:HAFEN_\xE4","NW_HARBOUR_STAIRS_01.TGA",asset::MaterialGroup::STONE));
  auto water = material("WATER","NW_WATER.TGA",asset::MaterialGroup::WATER);
  water.texture_anim_fps = 0.1f;
  water.wave_mode = asset::WaveMode::GROUND;
  water.wave_max_amplitude = 33.3f;
  water.disable_collision = true;
  water.color.a = 128;
  water.alpha_func = asset::AlphaFunction::BLEND;
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
    asset::PolygonFlagSet flags {};
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
  require(expected.materials.size()==3 && expected.vertices.size()==13,"canonical form keeps every source entry");
  require(back.vertices==13 && back.features==14 && back.materials.size()==3,"manifest counts");
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
  asset::StaticMesh mrm;
  mrm.positions = {{0,0,0},{100.5f,0,0},{0,-20.25f,3},{7,7,7}};
  mrm.alpha_test = 1;
  mrm.bbox.min = {0,-20.25f,0};
  mrm.bbox.max = {100.5f,0,3};
  mrm.sub_meshes.resize(2);
  mrm.sub_meshes[0].mat = material("EMPTY","EMPTY.TGA",asset::MaterialGroup::WOOD);
  mrm.sub_meshes[1].mat = material("BARREL_\xE4","BARREL.TGA",asset::MaterialGroup::WOOD);
  mrm.sub_meshes[1].wedges = {{{0,1,0},{0,0},0},{{0,1,0},{1,0},1},{{0.6f,0.8f,-0.0f},{0.5f,1},2},{{0,1,0},{0,0},2}};
  mrm.sub_meshes[1].triangles = {{{0,1,2}},{{2,1,3}}};
  writeBaseMesh(mrm,"TEST_MESH",dir/"mesh.glb");
  diff = compareBaseMeshes(mrm,readBaseMesh(dir/"mesh.glb"));
  require(diff.empty(),"mesh round trip: "+diff);

  // A font through ZenKit's own writer and reader: latin-1 name, UV bits outside 0..1,
  // -0.0, infinity and a NaN payload must reach the record and come back unchanged.
  {
  const float nan = std::bit_cast<float>(0x7FC12345u);
  zenkit::Font fnt("FONT_\xE4_TEST",17,{});
  fnt.glyphs.push_back({0,{{0.f,0.f},{0.f,0.f}}});
  fnt.glyphs.push_back({9,{{-0.0f,1.5f},{-0.25f,nan}}});
  fnt.glyphs.push_back({255,{{std::bit_cast<float>(0x7F800000u),0.125f},{1.f,2.f}}});
  std::vector<std::byte> bytes;
  fnt.save(zenkit::Write::to(&bytes).get());
  zenkit::Font loaded;
  loaded.load(zenkit::Read::from(&bytes).get());
  const auto own = asset::fromZenKit(loaded);
  require(own.name=="FONT_\xE4_TEST" && own.height==17 && own.glyphs.size()==3,"font header");
  require(gothicglb::bits(own.glyphs[1].uv[1].y)==0x7FC12345u && gothicglb::bits(own.glyphs[1].uv[0].x)==0x80000000u,
          "font UV bits through ZenKit");
  require(FontData::atlasName("FONT_\xE4_TEST.fnt")=="FONT_\xE4_TEST.TGA","font atlas name");
  const auto file = dir/"FONT_TEST.FNT.json";
  FontData::writeFont(own,"FONT_TEST.TGA",file);
  std::string atlas;
  const auto read = FontData::readFont(file,&atlas);
  diff = FontData::compare(own,read);
  require(diff.empty(),"font round trip: "+diff);
  require(atlas=="FONT_TEST.TGA" && gothicglb::bits(read.glyphs[1].uv[1].y)==0x7FC12345u,"font record bits");
  auto changed = read;
  changed.glyphs[2].uv[0].x = std::bit_cast<float>(0x7F800001u);
  require(!FontData::compare(own,changed).empty(),"font compare must see float bits");
  // A short binary and an unknown version are rejected, not read as a partial font.
  const auto bin = dir/"FONT_TEST.FNT.bin";
  fs::resize_file(bin,fs::file_size(bin)-1);
  rejected = false;
  try { FontData::readFont(file); } catch(const std::exception&) { rejected = true; }
  require(rejected,"truncated font binary must be rejected");
  FontData::writeFont(own,"FONT_TEST.TGA",file);
  auto json = gothicglb::readFile(file);
  const std::string text(json.begin(),json.end());
  const auto at = text.find("\"version\":1");
  require(at!=std::string::npos,"font record version");
  json[at+10] = uint8_t('2');
  gothicglb::writeFile(file,json.data(),json.size());
  rejected = false;
  try { FontData::readFont(file); } catch(const std::exception&) { rejected = true; }
  require(rejected,"unsupported font record version must be rejected");
  }

  // A program record: generated name, raw string bytes with \0 and \xFF, float bits (NaN payload,
  // -0.0, infinity), a member and a FUNCTION without values; through the codec and the native factory.
  {
  using zenkit::DaedalusDataType;
  namespace Flag = zenkit::DaedalusSymbolFlag;
  zenkit::DaedalusProgramRecord program;
  program.version = 50;
  auto& gen = program.symbols.emplace_back();
  gen.name = "$INSTANCE_HELP"; gen.generated = true; gen.type = DaedalusDataType::INSTANCE; gen.address = 0;
  auto& ints = program.symbols.emplace_back();
  ints.name = "INTS"; ints.type = DaedalusDataType::INT; ints.count = 2; ints.flags = Flag::CONST;
  ints.int_values = {-1,0x7FFFFFFF}; ints.file_index = 3; ints.char_start = 0xFFFFFF;
  auto& floats = program.symbols.emplace_back();
  floats.name = "FLOATS"; floats.type = DaedalusDataType::FLOAT; floats.count = 3;
  floats.float_bits = {0x7FC12345u,0x80000000u,0x7F800000u};
  auto& strings = program.symbols.emplace_back();
  strings.name = "STRINGS"; strings.type = DaedalusDataType::STRING; strings.count = 2;
  strings.string_values = {std::string("a\xFF\0b",4),""};
  auto& cls = program.symbols.emplace_back();
  cls.name = "C_TEST"; cls.type = DaedalusDataType::CLASS; cls.count = 1; cls.class_size = 4;
  auto& member = program.symbols.emplace_back();
  member.name = "C_TEST.VALUE"; member.type = DaedalusDataType::INT; member.count = 1; member.flags = Flag::MEMBER;
  member.parent = 4; member.member_offset = 0;
  auto& fn = program.symbols.emplace_back();
  fn.name = "FN"; fn.type = DaedalusDataType::FUNCTION; fn.flags = Flag::RETURN; fn.address = 1;
  fn.return_type = DaedalusDataType::INT;
  program.bytecode = {std::byte{0x4B},std::byte{0x00},std::byte{0xFF}};

  const auto programs = dir/"programs";
  fs::create_directories(programs);
  std::string name;
  const auto sha = ProgramData::writeProgram(program,programs,&name);
  const auto file = programs/(name+".json");
  std::string actual;
  const auto read = ProgramData::readProgram(file,&actual);
  require(actual==sha && ProgramData::recordSha256(file)==sha,"program record identity");
  diff = ProgramData::compare(program,read);
  require(diff.empty(),"program round trip: "+diff);
  require(read.symbols[2].float_bits[0]==0x7FC12345u && read.symbols[3].string_values[0]==std::string("a\xFF\0b",4),
          "program record raw bits");
  diff = ProgramData::compare(program,zenkit::DaedalusScript::from_program(read).export_program());
  require(diff.empty(),"program through the native factory: "+diff);
  auto changed = read;
  changed.symbols[2].float_bits[1] ^= 1u;
  require(!ProgramData::compare(program,changed).empty(),"program compare must see float bits");
  auto shape = read;
  shape.symbols[1].int_values.pop_back();
  rejected = false;
  try { (void)zenkit::DaedalusScript::from_program(shape); } catch(const std::exception&) { rejected = true; }
  require(rejected,"program value shape mismatch must be rejected");
  const auto bin = programs/(name+".bin");
  fs::resize_file(bin,fs::file_size(bin)-1);
  rejected = false;
  try { ProgramData::readProgram(file); } catch(const std::exception&) { rejected = true; }
  require(rejected,"truncated program binary must be rejected");
  }

  WorldData::selfTest(dir/"world-data");
  fs::remove_all(dir);
  std::cout << "self-test passed\n";
  return 0;
  }

}

int main(int argc, char** argv) {
  try {
    zenkit::Logger::set(zenkit::LogLevel::ERROR,[](auto level,const char* component,const char* message) {
      std::cerr << "ZenKit " << component << ": " << message << '\n';
      BaseImport::onZenkitLog(level,component,message);
      });
    const std::vector<std::string> args(argv+1,argv+argc);
    if(args.size()==1 && args[0]=="--self-test")
      return selfTest();
    if(args.size()==4 && args[0]=="world")
      return BaseImport::convertWorld(args[1],args[2],args[3]);
    if(args.size()==3 && args[0]=="base") {
      BaseImport::build(args[1],args[2]);
      return 0;
      }
    if(args.size()==3 && (args[0]=="zen" || args[0]=="verify-zen"))
      return BaseImport::convertZen(args[1],args[2],args[0]=="verify-zen");
    if(args.size()==3 && (args[0]=="meshes" || args[0]=="verify-meshes"))
      return BaseImport::convertMeshes(args[1],args[2],args[0]=="verify-meshes");
    if(args.size()==3 && (args[0]=="models" || args[0]=="verify-models"))
      return BaseImport::convertModels(args[1],args[2],args[0]=="verify-models");
    if(args.size()==3 && (args[0]=="fonts" || args[0]=="verify-fonts"))
      return BaseImport::convertFonts(args[1],args[2],args[0]=="verify-fonts");
    if(args.size()==3 && (args[0]=="programs" || args[0]=="verify-programs"))
      return BaseImport::convertPrograms(args[1],args[2],args[0]=="verify-programs");
    if(args.size()==3 && (args[0]=="sources" || args[0]=="verify-sources"))
      return BaseImport::convertSources(args[1],args[2],args[0]=="verify-sources");
    if(args.size()==3 && (args[0]=="textures" || args[0]=="verify-textures"))
      return BaseImport::convertTextures(args[1],args[2],args[0]=="verify-textures");
    if((args.size()==4 || args.size()==5) && args[0]=="verify-world") {
      return BaseImport::verifyWorld(args[1],args[2],args[3],args.size()==5 ? fs::path(args[4]) : fs::path());
      }
    std::cerr << "usage: content-convert world|verify-world <gothic dir> <WORLD.ZEN> <dir> [override]\n"
                 "       content-convert textures|verify-textures|meshes|verify-meshes|zen|verify-zen <gothic dir> <dir>\n"
                 "       content-convert models|verify-models <gothic dir> <dir>\n"
                 "       content-convert fonts|verify-fonts <gothic dir> <dir>\n"
                 "       content-convert programs|verify-programs <gothic dir> <dir>\n"
                 "       content-convert sources|verify-sources <gothic dir> <dir>\n"
                 "       content-convert base <gothic dir> <dir>\n"
                 "       content-convert --self-test\n";
    return 2;
    }
  catch(const std::exception& e) {
    std::cerr << "content-convert: " << e.what() << "\n";
    return 1;
    }
  }
