#include "knownroadsbench.h"
#include "knownroads.h"
#include "navigationeyebench.h"

#include "game/gamescript.h"
#include "world/world.h"
#include "world/objects/npc.h"

#if defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#endif
#include <rapidjson/document.h>
#if defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <limits>
#include <map>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

namespace {
using Tempest::Vec3;
using Clock=std::chrono::steady_clock;

struct Place {
  std::string id,kind,anchor;
  Vec3 center;
};
struct Routine {
  std::string point;
  int64_t start=0,end=0;
  size_t node=KnownRoads::invalid;
};
struct Actor {
  Npc* npc=nullptr;
  int64_t handleId=0;
  std::string selector;
  Vec3 spawn;
  std::vector<Routine> routines;
  KnownRoads::Knowledge knowledge;
  KnownRoads::Path lastPath;
  bool hasPath=false;
};
struct State {
  World* world=nullptr;
  std::string worldName,directory;
  std::vector<Place> places;
  std::unordered_map<std::string,size_t> pointPlace;
  std::unordered_map<std::string,size_t> nodeByName;
  std::unique_ptr<KnownRoads> graph;
  std::vector<Actor> actors;
} state;

std::string quote(std::string_view value) {
  rapidjson::StringBuffer buffer;
  rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
  writer.String(value.data(),rapidjson::SizeType(value.size()));
  return {buffer.GetString(),buffer.GetSize()};
}
std::string number(double value) {
  if(!std::isfinite(value)) return "null";
  std::ostringstream out;
  out << std::setprecision(10) << value;
  return out.str();
}
std::string vector(const Vec3& p) {
  return '['+number(p.x)+','+number(p.y)+','+number(p.z)+']';
}
std::string upper(std::string text) {
  for(char& c:text) if(c>='a' && c<='z') c=char(c-'a'+'A');
  return text;
}
bool digits(std::string_view value) {
  return !value.empty() && std::all_of(value.begin(),value.end(),[](char c){return c>='0' && c<='9';});
}
void append(const char* file,const std::string& row) {
  std::ofstream out(state.directory+'/'+file,std::ios::app);
  if(!out) throw std::runtime_error(std::string("cannot open roads journal ")+file);
  out << row << '\n';
  if(!out) throw std::runtime_error(std::string("cannot write roads journal ")+file);
}
std::string symbol(World& world,const Npc& npc) {
  const auto* sym=world.script().findSymbol(size_t(npc.instanceSymbol()));
  return sym?std::string(sym->name()):std::string{};
}
Npc& resolve(World& world,const std::string& selector) {
  Npc* npc=nullptr;
  if(selector=="player") npc=world.player();
  else if(digits(selector)) npc=NavigationEyeBench::actor(std::stoull(selector));
  else if(selector.starts_with("id:") && digits(selector.substr(3))) {
    const auto id=std::stoull(selector.substr(3));
    if(id<=std::numeric_limits<uint32_t>::max()) npc=world.npcById(uint32_t(id));
  } else {
    const auto sym=world.script().findSymbolIndex(upper(selector));
    if(sym!=size_t(-1)) npc=world.findNpcByInstance(sym);
  }
  if(!npc) throw std::runtime_error("roads actor unavailable: "+selector);
  return *npc;
}
bool live(const Actor& actor,World& world) {
  // NPC array indices are transient; validate identity before dereferencing.
  for(uint32_t id=0;id<world.npcCount();++id)
    if(auto* npc=world.npcById(id);npc==actor.npc) return npc->handle().id==actor.handleId;
  return false;
}
void requireGraph() {
  if(!state.graph) throw std::runtime_error("roads load <places.json> is required first");
}
size_t nodeFor(World& world,std::string_view name) {
  const auto it=state.nodeByName.find(upper(std::string(name)));
  if(it!=state.nodeByName.end()) return it->second;
  // TA routinely names a freepoint. It maps onto the nearest real graph node,
  // while the freepoint itself is never inserted into the road graph.
  if(const auto* point=world.findPoint(upper(std::string(name)),false)) return state.graph->nearest(point->position());
  return KnownRoads::invalid;
}
std::vector<Routine> routines(World& world,const Npc& npc) {
  std::vector<Routine> result;
  npc.forEachRoutine([&](const Npc::RoutineInfo& r){
    result.push_back({std::string(r.point),r.start.toInt(),r.end.toInt(),nodeFor(world,r.point)});
  });
  std::stable_sort(result.begin(),result.end(),[](const Routine& a,const Routine& b){return a.start<b.start;});
  return result;
}
std::vector<size_t> routineNodes(const std::vector<Routine>& points) {
  std::vector<size_t> result;
  for(const auto& point:points) if(point.node!=KnownRoads::invalid) result.push_back(point.node);
  return result;
}
std::string routineJson(const std::vector<Routine>& points) {
  std::ostringstream out;out << '[';
  for(size_t i=0;i<points.size();++i) {
    if(i) out << ',';
    const auto& r=points[i];
    out << "{\"point\":" << quote(r.point) << ",\"start_ms\":" << r.start << ",\"end_ms\":" << r.end
        << ",\"node\":" << (r.node==KnownRoads::invalid?"null":std::to_string(r.node)) << '}';
  }
  out << ']';return out.str();
}
size_t count(const std::vector<uint8_t>& mask) {
  return size_t(std::count_if(mask.begin(),mask.end(),[](uint8_t bit){return bit!=0;}));
}
std::string stats(const KnownRoads::Knowledge& k) {
  const auto n=count(k.nodes),e=count(k.edges);
  return "\"known_nodes\":"+std::to_string(n)+",\"known_edges\":"+std::to_string(e)+
      ",\"total_nodes\":"+std::to_string(k.nodes.size())+",\"total_edges\":"+std::to_string(k.edges.size())+
      ",\"node_fraction\":"+number(k.nodes.empty()?0:double(n)/double(k.nodes.size()))+
      ",\"edge_fraction\":"+number(k.edges.empty()?0:double(e)/double(k.edges.size()))+
      ",\"stranger\":"+(k.stranger?"true":"false");
}
std::string maskJson(const std::vector<uint8_t>& mask) {
  std::ostringstream out;out << '[';bool first=true;
  for(size_t i=0;i<mask.size();++i) if(mask[i]) {if(!first) out << ',';first=false;out << i;}
  out << ']';return out.str();
}
std::string indices(const std::vector<size_t>& values) {
  std::ostringstream out;out << '[';
  for(size_t i=0;i<values.size();++i) {if(i) out << ',';out << values[i];}
  out << ']';return out.str();
}
std::string pathJson(const KnownRoads::Path& p,const KnownRoads::Knowledge& k) {
  size_t unknown=0;
  for(auto edge:p.edges) if(edge>=k.edges.size() || !k.edges[edge]) ++unknown;
  std::ostringstream out;
  out << "{\"nodes\":" << indices(p.nodes) << ",\"edges\":" << indices(p.edges)
      << ",\"length_m\":" << number(p.lengthCm*.01) << ",\"remaining_m\":" << number(p.remainingCm*.01)
      << ",\"start_offset_m\":" << number(p.startOffsetCm*.01) << ",\"direction\":" << vector(p.direction)
      << ",\"goal_in_known\":" << (p.goalInKnown?"true":"false") << ",\"unknown_edge_count\":" << unknown;
  if(!p.nodes.empty()) {
    out << ",\"endpoint\":" << quote(state.graph->nodes()[p.nodes.back()].name)
        << ",\"endpoint_cm\":" << vector(state.graph->nodes()[p.nodes.back()].position);
  }
  out << '}';return out.str();
}

void graphJournal() {
  std::ofstream out(state.directory+"/roads-graph.json",std::ios::trunc);
  if(!out) throw std::runtime_error("cannot open roads graph journal");
  out << std::setprecision(9) << "{\"kind\":\"roads_graph\",\"world\":" << quote(state.worldName)
      << ",\"node_columns\":[\"name\",\"x_cm\",\"y_cm\",\"z_cm\"],\"nodes\":[";
  const auto& nodes=state.graph->nodes();
  for(size_t i=0;i<nodes.size();++i) {
    if(i) out << ',';
    const auto& node=nodes[i];
    out << '[' << quote(node.name) << ',' << node.position.x << ',' << node.position.y << ',' << node.position.z << ']';
  }
  out << "],\"edge_columns\":[\"a\",\"b\",\"length_cm\",\"major\"],\"edges\":[";
  const auto& edges=state.graph->edges();
  for(size_t i=0;i<edges.size();++i) {
    if(i) out << ',';
    const auto& edge=edges[i];
    out << '[' << edge.a << ',' << edge.b << ',' << edge.lengthCm << ',' << (edge.major?"true":"false") << ']';
  }
  out << "]}\n";
  if(!out) throw std::runtime_error("cannot write roads graph journal");
}
std::string load(World& world,const std::string& file) {
  std::ifstream in(file);
  if(!in) throw std::runtime_error("cannot read roads places: "+file);
  const std::string data((std::istreambuf_iterator<char>(in)),std::istreambuf_iterator<char>());
  rapidjson::Document json;json.Parse(data.data(),data.size());
  if(json.HasParseError() || !json.IsObject() || !json.HasMember("places") || !json["places"].IsArray() ||
     !json.HasMember("points") || !json["points"].IsObject()) throw std::runtime_error("invalid roads places book");
  if(json.HasMember("world") && json["world"].IsString() && upper(json["world"].GetString())!=upper(std::string(world.name())))
    throw std::runtime_error("roads places book is for a different world");
  std::vector<Place> places;
  for(const auto& p:json["places"].GetArray()) {
    if(!p.IsObject() || !p.HasMember("id") || !p["id"].IsString() || !p.HasMember("kind") || !p["kind"].IsString() ||
       !p.HasMember("center") || !p["center"].IsArray() || p["center"].Size()!=3)
      throw std::runtime_error("invalid roads place entry");
    Place place;place.id=p["id"].GetString();place.kind=p["kind"].GetString();
    for(size_t i=0;i<3;++i) if(!p["center"][rapidjson::SizeType(i)].IsNumber()) throw std::runtime_error("invalid roads place center");
    place.center=Vec3(p["center"][0].GetFloat(),p["center"][1].GetFloat(),p["center"][2].GetFloat());
    if(p.HasMember("anchor") && p["anchor"].IsString()) place.anchor=p["anchor"].GetString();
    places.push_back(std::move(place));
  }
  std::unordered_map<std::string,size_t> pointPlace;
  for(auto it=json["points"].MemberBegin();it!=json["points"].MemberEnd();++it) {
    if(!it->value.IsUint64() || it->value.GetUint64()>=places.size()) throw std::runtime_error("invalid roads point place index");
    pointPlace.emplace(upper(it->name.GetString()),size_t(it->value.GetUint64()));
  }
  std::vector<const WayPoint*> waypoints;
  world.forEachWayPoint([&](const WayPoint& point){if(!point.isFreePoint()) waypoints.push_back(&point);});
  std::sort(waypoints.begin(),waypoints.end(),[](const WayPoint* a,const WayPoint* b){return a->name<b->name;});
  std::vector<KnownRoads::Node> nodes;
  std::unordered_map<const WayPoint*,size_t> ids;
  std::unordered_map<std::string,size_t> nodeByName;
  for(const auto* point:waypoints) {
    const auto id=nodes.size();ids.emplace(point,id);nodeByName.emplace(point->name,id);
    nodes.push_back({point->name,point->position()});
  }
  const auto major=[&](size_t node){
    const auto it=pointPlace.find(nodes[node].name);
    return it!=pointPlace.end() && (places[it->second].kind=="road" || places[it->second].kind=="street");
  };
  std::map<std::pair<size_t,size_t>,KnownRoads::Edge> unique;
  for(size_t a=0;a<waypoints.size();++a) for(const auto& connection:waypoints[a]->connections()) {
    const auto it=ids.find(connection.point);
    if(it==ids.end() || a>=it->second) continue;
    const auto b=it->second;
    unique.emplace(std::make_pair(a,b),KnownRoads::Edge{a,b,float(connection.len),major(a)&&major(b)});
  }
  std::vector<KnownRoads::Edge> edges;edges.reserve(unique.size());
  for(const auto& entry:unique) edges.push_back(entry.second);
  auto graph=std::make_unique<KnownRoads>(std::move(nodes),std::move(edges));
  state.places=std::move(places);state.pointPlace=std::move(pointPlace);state.nodeByName=std::move(nodeByName);
  state.graph=std::move(graph);state.actors.clear();graphJournal();
  size_t majorEdges=0;
  for(const auto& edge:state.graph->edges()) majorEdges+=edge.major;
  return "{\"kind\":\"roads_loaded\",\"nodes\":"+std::to_string(state.graph->nodes().size())+
      ",\"edges\":"+std::to_string(state.graph->edges().size())+",\"major_edges\":"+std::to_string(majorEdges)+
      ",\"places\":"+std::to_string(state.places.size())+"}";
}

Vec3 goal(World& world,const std::string& name) {
  const auto key=upper(name);
  if(const auto* point=world.findPoint(key,false)) return point->position();
  for(const auto& place:state.places) if(upper(place.id)==key) {
    if(!place.anchor.empty()) if(const auto* point=world.findPoint(place.anchor,false)) return point->position();
    return place.center;
  }
  throw std::runtime_error("roads unknown place or waypoint: "+name);
}
Actor& tracked(World& world,const std::string& selector) {
  auto& npc=resolve(world,selector);
  for(auto& actor:state.actors) if(actor.npc==&npc && live(actor,world)) return actor;
  throw std::runtime_error("roads on|stranger is required for actor: "+selector);
}
std::string enable(World& world,const std::string& selector,bool stranger,const std::string& origin) {
  auto& npc=resolve(world,selector);
  Actor actor;actor.npc=&npc;actor.handleId=npc.handle().id;actor.selector=selector;
  actor.spawn=origin.empty()?npc.position():goal(world,origin);actor.routines=routines(world,npc);
  const auto points=routineNodes(actor.routines);
  const auto start=Clock::now();actor.knowledge=state.graph->build(points,actor.spawn,stranger);
  const auto cost=std::chrono::duration<double,std::micro>(Clock::now()-start).count();
  const std::string result="{\"kind\":\"roads_enabled\",\"actor\":"+quote(selector)+",\"instance\":"+quote(symbol(world,npc))+
      ",\"npc_id\":"+std::to_string(world.npcId(&npc))+",\"spawn_cm\":"+vector(actor.spawn)+','+stats(actor.knowledge)+
      ",\"build_us\":"+number(cost)+",\"routines\":"+routineJson(actor.routines)+"}";
  auto it=std::find_if(state.actors.begin(),state.actors.end(),[&](const Actor& old){return old.npc==&npc;});
  if(it==state.actors.end()) state.actors.push_back(std::move(actor));else *it=std::move(actor);
  return result;
}
std::string snapshot(World& world,const std::string& tag,const std::string& selector) {
  auto& actor=tracked(world,selector);
  const auto result="{\"kind\":\"roads_snapshot\",\"tag\":"+quote(tag)+",\"actor\":"+quote(selector)+
      ",\"instance\":"+quote(symbol(world,*actor.npc))+",\"npc_id\":"+std::to_string(world.npcId(actor.npc))+
      ",\"spawn_cm\":"+vector(actor.spawn)+','+stats(actor.knowledge)+",\"routines\":"+routineJson(actor.routines)+
      ",\"known_node_ids\":"+maskJson(actor.knowledge.nodes)+",\"known_edge_ids\":"+maskJson(actor.knowledge.edges)+
      (actor.hasPath?",\"last_path\":"+pathJson(actor.lastPath,actor.knowledge):std::string{})+"}";
  append("roads-snapshots.jsonl",result);
  return "{\"kind\":\"roads_snapshot_written\",\"tag\":"+quote(tag)+",\"actor\":"+quote(selector)+','+stats(actor.knowledge)+"}";
}
std::string add(World& world,const std::string& selector,const std::string& name) {
  auto& actor=tracked(world,selector);
  const auto before=count(actor.knowledge.edges);const auto key=upper(name);
  std::vector<size_t> destinations;
  for(size_t p=0;p<state.places.size();++p) if(upper(state.places[p].id)==key) {
    for(size_t n=0;n<state.graph->nodes().size();++n) {
      const auto it=state.pointPlace.find(state.graph->nodes()[n].name);
      if(it!=state.pointPlace.end() && it->second==p) destinations.push_back(n);
    }
    if(destinations.empty()) destinations.push_back(state.graph->nearest(goal(world,name)));
    break;
  }
  if(destinations.empty()) {
    const auto node=nodeFor(world,name);
    if(node==KnownRoads::invalid) throw std::runtime_error("roads unknown place or waypoint: "+name);
    destinations.push_back(node);
  }
  const bool connected=state.graph->add(actor.knowledge,destinations);actor.hasPath=false;
  return "{\"kind\":\"roads_added\",\"actor\":"+quote(selector)+",\"destination\":"+quote(name)+
      ",\"major_connection\":"+(connected?"true":"false")+",\"added_edges\":"+std::to_string(count(actor.knowledge.edges)-before)+','+stats(actor.knowledge)+"}";
}
std::string path(World& world,const std::string& selector,const std::string& destination,const std::string& origin) {
  auto& actor=tracked(world,selector);
  const auto start=origin.empty()?actor.npc->position():goal(world,origin);
  const auto target=goal(world,destination);
  const auto begin=Clock::now();actor.lastPath=state.graph->path(actor.knowledge,start,target);actor.hasPath=true;
  const auto cost=std::chrono::duration<double,std::micro>(Clock::now()-begin).count();
  KnownRoads::Knowledge all;
  all.nodes.assign(state.graph->nodes().size(),1);all.edges.assign(state.graph->edges().size(),1);
  const auto full=state.graph->path(all,start,target);
  const auto result="{\"kind\":\"roads_path\",\"actor\":"+quote(selector)+",\"goal\":"+quote(destination)+
      ",\"start_cm\":"+vector(start)+",\"goal_cm\":"+vector(target)+",\"query_us\":"+number(cost)+
      ",\"path\":"+pathJson(actor.lastPath,actor.knowledge)+",\"full_network\":"+pathJson(full,all)+"}";
  append("roads-paths.jsonl",result);return result;
}
std::string actors(World& world,const std::string& filter) {
  std::ostringstream out;out << "{\"kind\":\"roads_actors\",\"actors\":[";bool first=true;
  for(uint32_t id=0;id<world.npcCount();++id) if(auto* npc=world.npcById(id)) {
    const auto instance=symbol(world,*npc);
    if(!filter.empty() && upper(instance).find(upper(filter))==std::string::npos) continue;
    if(!first) out << ',';
    first=false;
    out << "{\"npc_id\":" << id << ",\"instance\":" << quote(instance)
        << ",\"position_cm\":" << vector(npc->position()) << ",\"routines\":" << routineJson(routines(world,*npc)) << '}';
  }
  out << "]}";return out.str();
}
std::string distribution(std::vector<double> values) {
  if(values.empty()) return "{}";
  double sum=0;
  for(auto v:values) sum+=v;
  std::sort(values.begin(),values.end());
  return "{\"samples\":"+std::to_string(values.size())+",\"median_us\":"+number(values[values.size()/2])+
      ",\"p95_us\":"+number(values[size_t(std::ceil(double(values.size())*.95))-1])+",\"mean_us\":"+number(sum/double(values.size()))+"}";
}
std::string bench(World& world,size_t wanted,const std::string& destination,size_t repeats) {
  if(!wanted || wanted>500 || !repeats || repeats>1000) throw std::runtime_error("roads bench count 1..500, repeats 1..1000");
  struct Input {Npc* npc;std::string instance;std::vector<size_t> routines;};
  std::vector<Input> inputs;
  for(uint32_t id=0;id<world.npcCount();++id) if(auto* npc=world.npcById(id)) {
    auto nodes=routineNodes(routines(world,*npc));
    if(!nodes.empty()) inputs.push_back({npc,symbol(world,*npc),std::move(nodes)});
  }
  std::sort(inputs.begin(),inputs.end(),[](const Input& a,const Input& b){return a.instance<b.instance;});
  if(inputs.size()<wanted) throw std::runtime_error("roads bench not enough routine NPCs");
  inputs.resize(wanted);const auto target=goal(world,destination);
  std::vector<double> builds,paths;builds.reserve(wanted*repeats);paths.reserve(wanted*repeats);
  std::ostringstream timing;
  for(size_t r=0;r<repeats;++r) for(const auto& input:inputs) {
    const auto start=Clock::now();auto knowledge=state.graph->build(input.routines,input.npc->position());
    const auto built=Clock::now();const auto route=state.graph->path(knowledge,input.npc->position(),target);
    const auto done=Clock::now();
    const auto buildUs=std::chrono::duration<double,std::micro>(built-start).count();
    const auto pathUs=std::chrono::duration<double,std::micro>(done-built).count();
    builds.push_back(buildUs);paths.push_back(pathUs);
    timing << "{\"kind\":\"roads_cost\",\"instance\":" << quote(input.instance) << ",\"repeat\":" << r
           << ",\"build_us\":" << number(buildUs) << ",\"path_us\":" << number(pathUs)
           << ",\"known_nodes\":" << count(knowledge.nodes) << ",\"known_edges\":" << count(knowledge.edges)
           << ",\"path_edges\":" << route.edges.size() << "}\n";
  }
  std::ofstream raw(state.directory+"/roads-timing.jsonl",std::ios::app);
  if(!raw) throw std::runtime_error("cannot open roads timing journal");
  raw << timing.str();
  if(!raw) throw std::runtime_error("cannot write roads timing journal");
  return "{\"kind\":\"roads_bench\",\"actors\":"+std::to_string(wanted)+",\"repeats\":"+std::to_string(repeats)+
      ",\"goal\":"+quote(destination)+",\"graph_build_included\":false,\"routine_mapping_included\":false,\"build\":"+distribution(builds)+
      ",\"path\":"+distribution(paths)+"}";
}
}

void KnownRoadsBench::reset() { state=State{}; }

void KnownRoadsBench::tick(World* world) {
  if(state.world==world && (!world || state.worldName==world->name())) return;
  // Loading or switching the world invalidates all NPC pointers and graph IDs.
  const auto directory=state.directory;state=State{};state.directory=directory;
  state.world=world;if(world) state.worldName=std::string(world->name());
}

std::string KnownRoadsBench::command(const std::vector<std::string>& args,World& world,const std::string& outputDirectory) {
  tick(&world);
  if(state.directory.empty()) {
    state.directory=outputDirectory;
    for(const char* file:{"roads-events.jsonl","roads-paths.jsonl","roads-snapshots.jsonl","roads-timing.jsonl"}) {
      std::ofstream out(state.directory+'/'+file,std::ios::trunc);
      if(!out) throw std::runtime_error(std::string("cannot initialize roads journal ")+file);
    }
  }
  std::string result;
  if(args.size()==3 && args[1]=="load") result=load(world,args[2]);
  else {
    requireGraph();
    if((args.size()==3 || args.size()==4) && (args[1]=="on" || args[1]=="stranger"))
      result=enable(world,args[2],args[1]=="stranger",args.size()==4?args[3]:std::string{});
    else if(args.size()==4 && args[1]=="add") result=add(world,args[2],args[3]);
    else if((args.size()==4 || args.size()==5) && args[1]=="path") result=path(world,args[2],args[3],args.size()==5?args[4]:std::string{});
    else if(args.size()==4 && args[1]=="snapshot") result=snapshot(world,args[2],args[3]);
    else if((args.size()==2 || args.size()==3) && args[1]=="actors") result=actors(world,args.size()==3?args[2]:std::string{});
    else if((args.size()==4 || args.size()==5) && args[1]=="bench") result=bench(world,std::stoull(args[2]),args[3],args.size()==5?std::stoull(args[4]):10);
    else throw std::runtime_error("roads: load <places.json> | on|stranger <actor> [spawn] | add <actor> <place|waypoint> | path <actor> <goal> [start] | snapshot <tag> <actor> | actors [filter] | bench <count> <goal> [repeats]");
  }
  append("roads-events.jsonl",result);return result;
}
