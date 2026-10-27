#include "videodata.h"
#include "videoformat.h"
#include "sha256.h"
#include "bink/video.h"
#include <zenkit/Vfs.hh>
#include <zenkit/Stream.hh>
#include <rapidjson/document.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>
#define MINIZ_NO_ZLIB_COMPATIBLE_NAMES
#include "miniz.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <exception>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <thread>

namespace fs=std::filesystem;
namespace VideoData {
namespace {
constexpr uint64_t HeaderSize=128;
constexpr int CompressionLevel=6;
constexpr uint32_t KeyframeInterval=8;
void require(bool ok,const std::string& reason) { if(!ok) throw std::runtime_error("video data: "+reason); }
void require(bool ok,const char* reason) { if(!ok) throw std::runtime_error(std::string("video data: ")+reason); }
uint32_t aligned(uint32_t n) { return ((n+15)/16)*16; }
std::string upper(std::string name) { for(auto& c:name) if(c>='a' && c<='z') c=char(c-'a'+'A'); return name; }
std::string quoted(const std::string& value) {
  rapidjson::StringBuffer b; rapidjson::Writer<rapidjson::StringBuffer> w(b); w.String(value.c_str(),rapidjson::SizeType(value.size())); return b.GetString();
  }
void writeBytes(std::ostream& out,const void* data,size_t size) {
  out.write(static_cast<const char*>(data),std::streamsize(size)); require(bool(out),"write failed");
  }
void put32(std::ostream& out,uint32_t value) { uint8_t b[4]; for(size_t i=0;i<4;++i) b[i]=uint8_t(value>>(i*8)); writeBytes(out,b,4); }
void put64(std::ostream& out,uint64_t value) { uint8_t b[8]; for(size_t i=0;i<8;++i) b[i]=uint8_t(value>>(i*8)); writeBytes(out,b,8); }
void readBytes(std::istream& in,void* data,size_t size) { in.read(static_cast<char*>(data),std::streamsize(size)); require(bool(in),"truncated file"); }
uint32_t get32(std::istream& in) { uint8_t b[4]; readBytes(in,b,4); uint32_t v=0; for(size_t i=0;i<4;++i) v|=uint32_t(b[i])<<(i*8); return v; }
uint64_t get64(std::istream& in) { uint8_t b[8]; readBytes(in,b,8); uint64_t v=0; for(size_t i=0;i<8;++i) v|=uint64_t(b[i])<<(i*8); return v; }
std::string hashRange(std::istream& in,uint64_t size) {
  Sha256 hash; std::array<char,1<<16> buffer;
  while(size) { const auto n=size_t(std::min<uint64_t>(size,buffer.size())); readBytes(in,buffer.data(),n); hash.update(buffer.data(),n); size-=n; }
  return hash.finish();
  }
std::string readText(const fs::path& path) {
  std::ifstream in(path,std::ios::binary); require(bool(in),"cannot open "+path.string()); return {std::istreambuf_iterator<char>(in),{}};
  }
std::string fileHash(const fs::path& path) { return BaseImport::sha256File(path); }
std::string frameHash(uint32_t flags,std::string_view data) {
  uint8_t bytes[4]; for(size_t i=0;i<4;++i) bytes[i]=uint8_t(flags>>(i*8));
  Sha256 hash; hash.update(bytes,sizeof(bytes)); hash.update(data.data(),data.size()); return hash.finish();
  }
void collect(const zenkit::VfsNode& node,const std::string& parent,std::map<const zenkit::VfsNode*,std::string>& paths,std::set<std::string>& names) {
  for(const auto& child:node.children()) {
    const auto path=parent+child.name();
    if(child.type()==zenkit::VfsNodeType::DIRECTORY) collect(child,path+"/",paths,names);
    else if(upper(fs::path(child.name()).extension().string())==".BIK") { paths[&child]=path; names.insert(upper(child.name())); }
    }
  }
std::vector<uint8_t> bytes(const zenkit::VfsNode& node) {
  auto in=node.open_read(); std::vector<uint8_t> data; std::array<uint8_t,1<<16> buffer;
  for(;;) { const size_t n=in->read(buffer.data(),buffer.size()); if(!n) break; data.insert(data.end(),buffer.begin(),buffer.begin()+ptrdiff_t(n)); }
  return data;
  }
struct Selected {
  zenkit::Vfs vfs;
  std::vector<Source> list;
  std::vector<const zenkit::VfsNode*> nodes;
  explicit Selected(const fs::path& gothic) {
    fs::path work; BaseImport::mountRuntime(vfs,gothic,work);
    std::map<const zenkit::VfsNode*,std::string> paths; std::set<std::string> names;
    collect(vfs.root(),"",paths,names);
    for(const auto& name:names) {
      const auto* node=vfs.find(name); require(node!=nullptr,"unresolved BIK "+name);
      auto in=node->open_read(); Sha256 hash; uint64_t size=0; std::array<char,1<<16> buffer;
      for(;;) { const auto n=in->read(buffer.data(),buffer.size()); if(!n) break; hash.update(buffer.data(),n); size+=n; }
      list.push_back({name,paths.at(node),size,hash.finish()}); nodes.push_back(node);
      }
    }
  };
Identity identityOf(const std::vector<Source>& list) {
  std::string json="{\"format_sha256\":"+quoted(VideoFormatSha256)+",\"sources\":[";
  for(size_t i=0;i<list.size();++i) {
    const auto& s=list[i]; json+=std::string(i ? "," : "")+"{\"name\":"+quoted(s.name)+",\"size\":"+std::to_string(s.size)+",\"sha256\":"+quoted(s.sha256)+"}";
    }
  json+="]}"; return {Sha256::hash(json),json};
  }
struct Input final:Bink::Video::Input {
  const std::vector<uint8_t>& data; size_t at=0;
  explicit Input(const std::vector<uint8_t>& d):data(d) {}
  void read(void* out,size_t n) override { require(at<=data.size() && n<=data.size()-at,"truncated BIK"); std::memcpy(out,data.data()+at,n); at+=n; }
  void seek(size_t n) override { require(n<=data.size(),"BIK seek past end"); at=n; }
  void skip(size_t n) override { require(at<=data.size() && n<=data.size()-at,"BIK skip past end"); at+=n; }
  };
int16_t pcm(float v) { return v < -1.00004566f ? int16_t(-32768) : (v > 1.00001514f ? int16_t(32767) : int16_t(v*32767.5f)); }
Info infoOf(const Bink::Video& video) {
  Info ret; const auto& frame=video.frame(0);
  ret.width=frame.width(); ret.height=frame.height(); ret.frames=uint32_t(video.frameCount()); ret.fpsNum=video.fps().num; ret.fpsDen=video.fps().den;
  for(uint8_t p=0;p<4;++p) ret.planes[p]={frame.plane(p).stride(),aligned((p==1 || p==2) ? ret.height/2 : ret.height)};
  for(size_t t=0;t<video.audioCount();++t) { const auto& a=video.audio(uint8_t(t)); ret.tracks.push_back({a.sampleRate,a.isMono ? 1u : 2u,0}); }
  return ret;
  }
struct Position { uint64_t offset=0, size=0; uint32_t flags=0; std::string sha256=std::string(64,'0'); };
void writeInfo(std::ostream& out,const Info& info,const std::vector<Position>& index,uint64_t fileSize,const std::string& hash) {
  out.seekp(0); writeBytes(out,"GVIDEO2\0",8); put32(out,2); put32(out,uint32_t(HeaderSize+32+16*info.tracks.size()+84*index.size())); put64(out,fileSize);
  writeBytes(out,hash.data(),64); put32(out,info.width); put32(out,info.height); put32(out,info.frames); put32(out,info.fpsNum); put32(out,info.fpsDen); put32(out,uint32_t(info.tracks.size()));
  const uint8_t reserved[16]={}; writeBytes(out,reserved,16);
  for(const auto& p:info.planes) { put32(out,p.stride); put32(out,p.rows); }
  for(const auto& t:info.tracks) { put32(out,t.sampleRate); put32(out,t.channels); put64(out,t.samples); }
  for(const auto& f:index) { put64(out,f.offset); put64(out,f.size); put32(out,f.flags); writeBytes(out,f.sha256.data(),64); }
  }
bool encodePlane(std::ostream& out,const uint8_t* data,size_t size,uint32_t stride,const std::vector<uint8_t>* previous) {
  std::vector<uint8_t> predicted(size);
  for(size_t i=0;i<size;++i) predicted[i]=uint8_t(data[i]-(i>=stride ? data[i-stride] : 0));
  mz_ulong length=mz_compressBound(mz_ulong(size)); std::vector<uint8_t> compressed(length);
  require(mz_compress2(compressed.data(),&length,predicted.data(),mz_ulong(size),CompressionLevel)==MZ_OK,"deflate failed"); compressed.resize(length);
  bool temporal=false;
  if(previous) {
    require(previous->size()==size,"previous plane size differs");
    for(size_t i=0;i<size;++i) predicted[i]=uint8_t(data[i]-(*previous)[i]);
    mz_ulong deltaSize=mz_compressBound(mz_ulong(size)); std::vector<uint8_t> delta(deltaSize);
    require(mz_compress2(delta.data(),&deltaSize,predicted.data(),mz_ulong(size),CompressionLevel)==MZ_OK,"deflate failed");
    if(deltaSize<compressed.size()) { delta.resize(deltaSize); compressed=std::move(delta); temporal=true; }
    }
  std::vector<uint8_t> restored(size); mz_ulong restoredSize=mz_ulong(size);
  require(mz_uncompress(restored.data(),&restoredSize,compressed.data(),mz_ulong(compressed.size()))==MZ_OK && restoredSize==size,"deflate verification failed");
  if(temporal) {
    for(size_t i=0;i<size;++i) restored[i]=uint8_t(restored[i]+(*previous)[i]);
    } else {
    for(size_t i=stride;i<size;++i) restored[i]=uint8_t(restored[i]+restored[i-stride]);
    }
  require(std::memcmp(restored.data(),data,size)==0,"decoded plane differs after compression");
  put32(out,uint32_t(compressed.size())); writeBytes(out,compressed.data(),compressed.size()); return temporal;
  }
Entry encode(const Source& source,Bink::Video& video,const fs::path& out) {
  Entry entry; entry.source=source; entry.file=source.name+".gvideo"; entry.info=infoOf(video);
  const auto file=out/entry.file; std::fstream output(file,std::ios::binary|std::ios::in|std::ios::out|std::ios::trunc); require(bool(output),"cannot write "+file.string());
  std::vector<Position> positions(entry.info.frames);
  writeInfo(output,entry.info,positions,0,std::string(64,'0'));
  Sha256 payloadHash; std::array<std::vector<uint8_t>,4> previousPlanes;
  for(uint32_t f=0;f<entry.info.frames;++f) {
    const Bink::Frame* decoded=nullptr;
    try { decoded=&video.nextFrame(); }
    catch(const std::exception& e) {
      throw std::runtime_error("video data: "+source.name+" frame "+std::to_string(f)+" (zero-based): "+e.what());
      }
    const auto& frame=*decoded; const uint64_t start=uint64_t(output.tellp());
    std::ostringstream encodedFrame(std::ios::binary|std::ios::out);
    uint32_t flags=0;
    for(uint8_t p=0;p<4;++p) {
      const auto& pi=entry.info.planes[p]; const auto* data=frame.plane(p).data(); const size_t size=size_t(pi.stride)*pi.rows;
      if(encodePlane(encodedFrame,data,size,pi.stride,f%KeyframeInterval ? &previousPlanes[p] : nullptr)) flags|=1u<<p;
      previousPlanes[p].assign(data,data+size);
      }
    for(size_t t=0;t<entry.info.tracks.size();++t) {
      const auto& samples=frame.audio(uint8_t(t)).samples; put32(encodedFrame,uint32_t(samples.size()));
      std::vector<uint8_t> encoded(samples.size()*2);
      for(size_t i=0;i<samples.size();++i) { const uint16_t v=uint16_t(pcm(samples[i])); encoded[2*i]=uint8_t(v); encoded[2*i+1]=uint8_t(v>>8); }
      for(size_t i=0;i<samples.size();++i) require(int16_t(uint16_t(encoded[2*i])|(uint16_t(encoded[2*i+1])<<8))==pcm(samples[i]),"PCM verification differs");
      writeBytes(encodedFrame,encoded.data(),encoded.size()); entry.info.tracks[t].samples+=samples.size();
      }
    const auto encoded=encodedFrame.str(); payloadHash.update(encoded.data(),encoded.size());
    positions[f]={start,encoded.size(),flags,frameHash(flags,encoded)}; writeBytes(output,encoded.data(),encoded.size());
    }
  entry.size=uint64_t(output.tellp()); const auto hash=payloadHash.finish();
  writeInfo(output,entry.info,positions,entry.size,hash); output.close(); entry.sha256=fileHash(file); return entry;
  }
std::string infoJson(const Info& info) {
  std::string json="{\"width\":"+std::to_string(info.width)+",\"height\":"+std::to_string(info.height)+",\"frames\":"+std::to_string(info.frames)+",\"fps_num\":"+std::to_string(info.fpsNum)+",\"fps_den\":"+std::to_string(info.fpsDen)+",\"planes\":[";
  for(size_t i=0;i<4;++i) json+=std::string(i ? "," : "")+"["+std::to_string(info.planes[i].stride)+","+std::to_string(info.planes[i].rows)+"]";
  json+="],\"tracks\":[";
  for(size_t i=0;i<info.tracks.size();++i) { const auto& t=info.tracks[i]; json+=std::string(i ? "," : "")+"{\"sample_rate\":"+std::to_string(t.sampleRate)+",\"channels\":"+std::to_string(t.channels)+",\"samples\":"+std::to_string(t.samples)+"}"; }
  return json+"]}";
  }
void writeIndex(const fs::path& out,const Identity& id,const std::vector<Entry>& entries) {
  std::string json="{\"version\":1,\"fingerprint\":"+quoted(id.fingerprint)+",\"identity\":"+quoted(id.json)+",\"videos\":[";
  for(size_t i=0;i<entries.size();++i) {
    const auto& e=entries[i]; json+=std::string(i ? "," : "")+"{\"name\":"+quoted(e.source.name)+",\"path\":"+quoted(e.source.path.generic_string())+",\"source_size\":"+std::to_string(e.source.size)+",\"source_sha256\":"+quoted(e.source.sha256)+",\"file\":"+quoted(e.file)+",\"size\":"+std::to_string(e.size)+",\"sha256\":"+quoted(e.sha256)+",\"info\":"+infoJson(e.info)+"}";
    }
  json+="]";
  const auto checksum=Sha256::hash(json); json+=",\"index_sha256\":\""+checksum+"\"}\n";
  std::ofstream file(out/"videos.json",std::ios::binary); writeBytes(file,json.data(),json.size());
  }
rapidjson::Document document(const fs::path& file) {
  const auto text=readText(file);
  constexpr std::string_view marker=",\"index_sha256\":\"", ending="\"}\n";
  const size_t trailer=marker.size()+64+ending.size();
  require(text.size()>=trailer,"missing index SHA-256");
  const size_t prefixSize=text.size()-trailer;
  require(text.compare(prefixSize,marker.size(),marker)==0 &&
          text.compare(text.size()-ending.size(),ending.size(),ending)==0,"invalid index SHA-256 trailer");
  require(Sha256::hash(std::string_view(text.data(),prefixSize))==text.substr(prefixSize+marker.size(),64),"index SHA-256 differs");
  rapidjson::Document doc; doc.Parse(text.data(),text.size()); require(!doc.HasParseError() && doc.IsObject(),"invalid videos.json");
  size_t checksums=0;
  for(auto member=doc.MemberBegin();member!=doc.MemberEnd();++member)
    if(std::string_view(member->name.GetString(),member->name.GetStringLength())=="index_sha256") ++checksums;
  require(checksums==1,"repeated index SHA-256");
  return doc;
  }
const rapidjson::Value& member(const rapidjson::Value& object,const char* key) { require(object.IsObject() && object.HasMember(key),std::string("missing ")+key); return object[key]; }
std::string str(const rapidjson::Value& object,const char* key) { const auto& v=member(object,key); require(v.IsString(),std::string("invalid ")+key); return {v.GetString(),v.GetStringLength()}; }
uint64_t number(const rapidjson::Value& object,const char* key) { const auto& v=member(object,key); require(v.IsUint64(),std::string("invalid ")+key); return v.GetUint64(); }
Info parseInfo(const rapidjson::Value& object) {
  Info info; info.width=uint32_t(number(object,"width")); info.height=uint32_t(number(object,"height")); info.frames=uint32_t(number(object,"frames")); info.fpsNum=uint32_t(number(object,"fps_num")); info.fpsDen=uint32_t(number(object,"fps_den"));
  const auto& planes=member(object,"planes"); require(planes.IsArray() && planes.Size()==4,"invalid planes");
  for(rapidjson::SizeType i=0;i<4;++i) { const auto& p=planes[i]; require(p.IsArray() && p.Size()==2 && p[0].IsUint() && p[1].IsUint(),"invalid plane dimensions"); info.planes[i]={p[0].GetUint(),p[1].GetUint()}; }
  const auto& tracks=member(object,"tracks"); require(tracks.IsArray(),"invalid tracks");
  for(const auto& t:tracks.GetArray()) info.tracks.push_back({uint32_t(number(t,"sample_rate")),uint32_t(number(t,"channels")),number(t,"samples")});
  return info;
  }
void compare(const fs::path& file,Bink::Video& video) {
  Reader reader(file); reader.verifyPayload(); const auto expected=infoOf(video); const auto& info=reader.info();
  require(info.width==expected.width && info.height==expected.height && info.frames==expected.frames && info.fpsNum==expected.fpsNum && info.fpsDen==expected.fpsDen && info.tracks.size()==expected.tracks.size(),"BIK metadata differs");
  std::vector<uint64_t> samples(info.tracks.size());
  for(size_t t=0;t<samples.size();++t) require(info.tracks[t].sampleRate==expected.tracks[t].sampleRate && info.tracks[t].channels==expected.tracks[t].channels,"BIK audio metadata differs");
  for(uint32_t f=0;f<info.frames;++f) {
    const auto& original=video.nextFrame(); const auto native=reader.nextFrame();
    for(uint8_t p=0;p<4;++p) { require(info.planes[p].stride==expected.planes[p].stride && info.planes[p].rows==expected.planes[p].rows,"BIK plane layout differs"); require(std::memcmp(native.planes[p].data(),original.plane(p).data(),native.planes[p].size())==0,"BIK plane differs at frame "+std::to_string(f)); }
    for(size_t t=0;t<samples.size();++t) {
      const auto& a=original.audio(uint8_t(t)).samples; require(a.size()==native.audio[t].size(),"BIK sample count differs");
      for(size_t s=0;s<a.size();++s) require(pcm(a[s])==native.audio[t][s],"BIK PCM differs");
      samples[t]+=a.size();
      }
    }
  for(size_t t=0;t<samples.size();++t) require(samples[t]==info.tracks[t].samples,"BIK total samples differ");
  }
}

std::string formatSha256() { return VideoFormatSha256; }
std::vector<Source> sources(const fs::path& gothic) { return Selected(gothic).list; }
Identity identify(const fs::path& gothic) { return identityOf(sources(gothic)); }
std::vector<Entry> readIndex(const fs::path& dir) {
  const auto doc=document(dir/"videos.json"); require(number(doc,"version")==1,"unsupported index version"); const auto& list=member(doc,"videos"); require(list.IsArray(),"invalid video list");
  std::vector<Entry> result; std::set<std::string> names;
  for(const auto& v:list.GetArray()) {
    Entry e; e.source={str(v,"name"),str(v,"path"),number(v,"source_size"),str(v,"source_sha256")}; e.file=str(v,"file"); e.sha256=str(v,"sha256"); e.size=number(v,"size"); e.info=parseInfo(member(v,"info"));
    require(!e.source.name.empty() && e.source.name==upper(e.source.name) && fs::path(e.source.name).filename()==e.source.name && e.file==e.source.name+".gvideo" && names.insert(e.source.name).second,"invalid or repeated video name");
    require(e.sha256.size()==64 && e.source.sha256.size()==64,"invalid SHA-256"); result.push_back(std::move(e));
    }
  return result;
  }
bool matches(const fs::path& dir,const Identity& identity) {
  try {
    const auto doc=document(dir/"videos.json"); if(str(doc,"fingerprint")!=identity.fingerprint || str(doc,"identity")!=identity.json) return false;
    const auto entries=readIndex(dir); std::vector<Source> list;
    for(const auto& e:entries) { if(fs::file_size(dir/e.file)!=e.size || fileHash(dir/e.file)!=e.sha256) return false; list.push_back(e.source); }
    return identityOf(list).json==identity.json;
    } catch(const std::exception&) { return false; }
  }
Reader::Reader(const fs::path& file):input(file,std::ios::binary) {
  require(bool(input),"cannot open "+file.string()); char magic[8]; readBytes(input,magic,8); require(std::memcmp(magic,"GVIDEO2\0",8)==0 && get32(input)==2,"invalid container header");
  payloadStart=get32(input); fileSize=get64(input); require(fs::file_size(file)==fileSize && payloadStart<=fileSize,"container size differs");
  char expectedHash[64]; readBytes(input,expectedHash,64); payloadSha256.assign(expectedHash,64); meta.width=get32(input); meta.height=get32(input); meta.frames=get32(input); meta.fpsNum=get32(input); meta.fpsDen=get32(input); const auto tracks=get32(input); char reserved[16]; readBytes(input,reserved,16);
  require(meta.width>0 && meta.height>0 && meta.width<65536 && meta.height<65536 && meta.frames>0 && meta.fpsNum>0 && meta.fpsDen>0 && tracks<=255,"invalid video metadata");
  require(uint64_t(payloadStart)==HeaderSize+32+16*uint64_t(tracks)+84*uint64_t(meta.frames),"invalid frame index size");
  for(size_t p=0;p<4;++p) { auto& plane=meta.planes[p]; plane={get32(input),get32(input)}; const bool chroma=p==1 || p==2; require(plane.stride==aligned(chroma ? meta.width/2 : meta.width) && plane.rows==aligned(chroma ? meta.height/2 : meta.height),"invalid plane dimensions"); }
  for(uint32_t t=0;t<tracks;++t) { Track track{get32(input),get32(input),get64(input)}; require(track.sampleRate>0 && (track.channels==1 || track.channels==2),"invalid audio metadata"); meta.tracks.push_back(track); }
  uint64_t end=payloadStart;
  for(uint32_t f=0;f<meta.frames;++f) {
    Position p{get64(input),get64(input),get32(input),{}}; char hash[64]; readBytes(input,hash,64); p.sha256.assign(hash,64);
    require((p.flags&~15u)==0 && (f%KeyframeInterval!=0 || p.flags==0),"invalid plane predictor flags");
    require(p.offset==end && p.size<=fileSize-p.offset,"invalid frame offset"); end+=p.size; index.push_back(p);
    }
  require(end==fileSize,"unindexed payload bytes");
  }
Frame Reader::readFrame(uint32_t number) {
  require(number<meta.frames,"frame number out of range");
  std::array<uint32_t,4> first; first.fill(number);
  if(!havePrevious || number!=next)
    for(size_t p=0;p<4;++p)
      while(index[first[p]].flags&(1u<<p)) --first[p];
  havePrevious=false;
  Frame ret; ret.audio.resize(meta.tracks.size());
  for(uint32_t frame=*std::min_element(first.begin(),first.end());frame<=number;++frame) {
    const auto& position=index[frame]; input.clear(); input.seekg(std::streamoff(position.offset));
    std::string encoded(size_t(position.size),'\0'); readBytes(input,encoded.data(),encoded.size());
    require(frameHash(position.flags,encoded)==position.sha256,"frame payload SHA-256 differs");
    std::istringstream frameInput(encoded,std::ios::binary|std::ios::in); uint64_t remaining=position.size;
    for(size_t p=0;p<4;++p) {
      require(remaining>=4,"truncated plane size"); const uint32_t size=get32(frameInput); remaining-=4; require(size<=remaining,"truncated plane"); remaining-=size;
      if(frame<first[p]) { frameInput.seekg(size,std::ios::cur); continue; }
      std::vector<uint8_t> compressed(size); readBytes(frameInput,compressed.data(),size); const auto& pi=meta.planes[p];
      std::vector<uint8_t> plane(size_t(pi.stride)*pi.rows); mz_ulong decoded=mz_ulong(plane.size());
      require(mz_uncompress(plane.data(),&decoded,compressed.data(),size)==MZ_OK && decoded==plane.size(),"invalid deflate plane");
      if(position.flags&(1u<<p)) {
        require(previousPlanes[p].size()==plane.size(),"missing temporal reference plane");
        for(size_t i=0;i<plane.size();++i) plane[i]=uint8_t(plane[i]+previousPlanes[p][i]);
        } else {
        for(size_t i=pi.stride;i<plane.size();++i) plane[i]=uint8_t(plane[i]+plane[i-pi.stride]);
        }
      previousPlanes[p]=std::move(plane);
      }
    for(auto& track:ret.audio) {
      require(remaining>=4,"truncated PCM size"); const uint32_t samples=get32(frameInput); remaining-=4; require(uint64_t(samples)*2<=remaining,"truncated PCM"); remaining-=uint64_t(samples)*2;
      if(frame!=number) { frameInput.seekg(std::streamoff(uint64_t(samples)*2),std::ios::cur); continue; }
      std::vector<uint8_t> pcmBytes(size_t(samples)*2); readBytes(frameInput,pcmBytes.data(),pcmBytes.size()); track.resize(samples);
      for(size_t i=0;i<samples;++i) track[i]=int16_t(uint16_t(pcmBytes[2*i])|(uint16_t(pcmBytes[2*i+1])<<8));
      }
    require(remaining==0,"trailing frame bytes");
    }
  ret.planes=previousPlanes; next=number+1; havePrevious=true; return ret;
  }
Frame Reader::nextFrame() { return readFrame(next); }
void Reader::verifyPayload() { input.clear(); input.seekg(std::streamoff(payloadStart)); require(hashRange(input,fileSize-payloadStart)==payloadSha256,"payload SHA-256 differs"); }
void build(const fs::path& gothic,const fs::path& out,const BaseImport::Progress& progress) {
  const auto started=std::chrono::steady_clock::now(); Selected selected(gothic); const auto identity=identityOf(selected.list); fs::create_directories(out);
  struct Job { std::vector<uint8_t> bytes; std::unique_ptr<Input> input; std::unique_ptr<Bink::Video> video; };
  std::vector<Job> jobs(selected.list.size());
  // Bink constructors write global VLC and cosine tables. Complete all constructors before decoding
  // on worker threads.
  for(size_t i=0;i<jobs.size();++i) { auto& job=jobs[i]; job.bytes=bytes(*selected.nodes[i]); require(Sha256::hash({reinterpret_cast<const char*>(job.bytes.data()),job.bytes.size()})==selected.list[i].sha256,"BIK changed while importing"); job.input=std::make_unique<Input>(job.bytes); job.video=std::make_unique<Bink::Video>(job.input.get(),selected.list[i].name); }
  const size_t threads=std::min<size_t>(12,std::max<size_t>(1,jobs.size())); std::vector<Entry> entries(jobs.size()); std::atomic<size_t> cursor=0; std::exception_ptr error; std::mutex sync;
  const auto run=[&] {
    try { for(;;) { const size_t i=cursor.fetch_add(1); if(i>=jobs.size()) break; entries[i]=encode(selected.list[i],*jobs[i].video,out); } }
    catch(...) { std::lock_guard<std::mutex> guard(sync); if(!error) error=std::current_exception(); cursor=jobs.size(); }
    };
  std::vector<std::thread> workers; for(size_t i=0;i<threads;++i) workers.emplace_back(run); for(auto& worker:workers) worker.join(); if(error) std::rethrow_exception(error);
  writeIndex(out,identity,entries);
  uint64_t total=fs::file_size(out/"videos.json");
  for(const auto& e:entries) { total+=e.size; std::cout<<"video "<<e.source.name<<" frames="<<e.info.frames<<" fps="<<e.info.fpsNum<<"/"<<e.info.fpsDen<<" dimensions="<<e.info.width<<"x"<<e.info.height<<" duration_s="<<(double(e.info.frames)*e.info.fpsDen/e.info.fpsNum)<<" bytes="<<e.size; for(size_t t=0;t<e.info.tracks.size();++t) std::cout<<" track"<<t<<"="<<e.info.tracks[t].samples<<"@"<<e.info.tracks[t].sampleRate<<"x"<<e.info.tracks[t].channels; std::cout<<"\n"; }
  const auto elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count();
  const auto report="videos: "+std::to_string(entries.size())+" files, "+std::to_string(total)+" bytes, "+std::to_string(elapsed)+" s, "+std::to_string(threads)+" threads"; std::cout<<report<<"\n"; if(progress) progress(report);
  }
int convert(const fs::path& gothic,const fs::path& out,bool verifyOnly) {
  if(!verifyOnly) { build(gothic,out); return 0; }
  Selected selected(gothic); require(matches(out,identityOf(selected.list)),"video part does not match installation"); const auto entries=readIndex(out); require(entries.size()==selected.list.size(),"video corpus size differs");
  for(size_t i=0;i<entries.size();++i) { auto data=bytes(*selected.nodes[i]); Input input(data); Bink::Video video(&input,selected.list[i].name); compare(out/entries[i].file,video); }
  std::cout<<"videos: verified "<<entries.size()<<" files, every plane and PCM sample\n"; return 0;
  }
}
