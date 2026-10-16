#include "game/knownroads.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace {
using Roads = KnownRoads;
using Tempest::Vec3;

void require(bool condition, const char* message) {
  if(condition)
    return;
  std::fprintf(stderr,"FAIL: %s\n",message);
  std::exit(EXIT_FAILURE);
  }

void close(double actual, double expected, const char* message) {
  require(std::isfinite(actual) && std::abs(actual-expected)<.01,message);
  }

std::vector<Roads::Node> nodes() {
  return {{"gate",{0,0,0}}, {"street1",{10000,0,0}}, {"street2",{20000,0,0}},
          {"street3",{30000,0,0}}, {"street4",{40000,0,0}}, {"home",{20000,0,3000}},
          {"alley1",{15000,0,3000}}, {"alley2",{5000,0,3000}}, {"courtyard",{15000,0,3500}},
          {"near_but_detour",{20001,0,3000}}, {"island1",{100,0,50}}, {"island2",{200,0,50}}};
  }

std::vector<Roads::Edge> edges() {
  return {{0,1,10000,true}, {1,2,10000,true}, {2,3,10000,true}, {3,4,10000,true},
          {2,5,3000,false}, {5,6,5000,false}, {6,7,10000,false}, {7,0,6000,false},
          {6,8,500,false}, {5,9,4100,false}, {10,11,100,false}};
  }

void familiarEdgesOnly(const Roads& roads, const Roads::Knowledge& knowledge, const Roads::Path& path) {
  require(path.nodes.size()==path.edges.size()+1,"nonempty path has one more node than edges");
  double length = 0;
  for(size_t i=0; i<path.edges.size(); ++i) {
    const size_t id = path.edges[i];
    require(id<knowledge.edges.size() && knowledge.edges[id],"every returned edge is familiar");
    const auto& edge = roads.edges()[id];
    require((edge.a==path.nodes[i] && edge.b==path.nodes[i+1]) ||
            (edge.b==path.nodes[i] && edge.a==path.nodes[i+1]),"edge joins the returned consecutive nodes");
    require(knowledge.nodes[path.nodes[i]] && knowledge.nodes[path.nodes[i+1]],"returned nodes are familiar");
    length += edge.lengthCm;
    }
  close(path.lengthCm,length,"reported length is sum of returned edges");
  }

void localAndStranger() {
  const Roads roads(nodes(),edges());
  const Vec3 start = roads.nodes()[0].position;
  const Vec3 goal = roads.nodes()[5].position;
  const auto local = roads.build({0,5},start);
  const auto stranger = roads.build({},start);
  require(!local.stranger && stranger.stranger,"routine distinguishes local from stranger");
  require(local.nodes[2] && local.edges[4],"30 m routine neighbourhood reaches road by network distance");
  require(!local.nodes[9] && !local.edges[9],"41 m path stays outside 40 m despite 1 cm straight separation");
  require(local.edges[5] && local.edges[6] && local.edges[7],"daily route teaches the shorter alley");
  require(stranger.nodes[3] && stranger.edges[2],"major road exactly 300 m away is familiar");
  require(!stranger.nodes[4] && !stranger.edges[3],"major road beyond 300 m is unknown");
  for(size_t i=0; i<roads.edges().size(); ++i)
    require(!stranger.edges[i] || roads.edges()[i].major,"stranger knows only major edges");

  const auto a = roads.path(local,start,goal);
  const auto b = roads.path(stranger,start,goal);
  familiarEdgesOnly(roads,local,a);
  familiarEdgesOnly(roads,stranger,b);
  require(a.goalInKnown && !b.goalInKnown,"goal matches only local's reachable familiar node");
  require(a.edges==std::vector<size_t>({7,6,5}),"local cuts through the daily alley");
  close(a.lengthCm,21000,"local path length");
  close(a.remainingCm,0,"local reaches goal");
  close(b.lengthCm,20000,"stranger follows main street");
  close(b.remainingCm,3000,"stranger leaves 30 m to the eyes");
  close(b.direction.x,0,"remainder x direction");
  close(b.direction.z,1,"remainder z direction");
  const auto forced = roads.build({0,5},start,true);
  require(forced.nodes==stranger.nodes && forced.edges==stranger.edges,"explicit stranger ignores local routine");
  std::puts("local/stranger: 210+0 m alley versus 200+30 m main street; 40/300 m network boundaries");
  }

void unknownGoalAndAdd() {
  const Roads roads(nodes(),edges());
  const Vec3 start = roads.nodes()[0].position;
  auto local = roads.build({0,5},start);
  const auto frontier = roads.path(local,start,roads.nodes()[8].position);
  familiarEdgesOnly(roads,local,frontier);
  require(frontier.nodes.back()==6 && !frontier.goalInKnown,"unknown courtyard ends at closest familiar node");
  close(frontier.remainingCm,500,"unknown goal leaves five metres");

  auto stranger = roads.build({},start);
  require(roads.add(stranger,{5}),"added home connects to existing major road");
  const auto home = roads.path(stranger,start,roads.nodes()[5].position);
  familiarEdgesOnly(roads,stranger,home);
  close(home.lengthCm,23000,"added place uses main road and its local approach");
  close(home.remainingCm,0,"addition removes the 30 m remainder");
  require(home.goalInKnown && !stranger.edges[5],"addition does not teach a remote alley shortcut");
  require(roads.add(stranger,{4}),"added distant street learns major connector beyond original range");
  require(stranger.edges[3],"added route really grows major road knowledge");
  const auto far = roads.path(stranger,start,roads.nodes()[4].position);
  familiarEdgesOnly(roads,stranger,far);
  close(far.lengthCm,40000,"added distant street route length");
  require(!roads.add(local,{10}),"disconnected place has no major connector");
  const auto island = roads.path(local,start,roads.nodes()[10].position);
  familiarEdgesOnly(roads,local,island);
  require(local.nodes[10] && local.nodes[11],"disconnected destination neighbourhood is remembered");
  require(island.nodes.back()==0 && island.goalInKnown,"known disconnected island stays familiar but query cannot jump to it");
  std::puts("frontier/add: unknown remainder 5 m; added home 230+0 m; distant major connector 400 m; islands stay unreachable");
  }

void acquisitionAndDeterminism() {
  const Roads cycle({{"a",{0,0,0}},{"b",{10000,0,0}},{"c",{5000,0,8000}}},
                    {{0,1,10000,false},{1,2,10000,false},{2,0,10000,false}});
  const auto daily = cycle.build({0,1,2},{0,0,0});
  require(std::all_of(daily.edges.begin(),daily.edges.end(),[](uint8_t v){return v!=0;}),
          "daily routine includes last-to-first trip");

  const Roads roads(nodes(),edges());
  const auto reference = roads.build({0,5},{0,0,0});
  const auto route = roads.path(reference,{0,0,0},roads.nodes()[8].position);
  for(int i=0; i<16; ++i) {
    const auto rebuilt = roads.build({0,5},{0,0,0});
    require(rebuilt.nodes==reference.nodes && rebuilt.edges==reference.edges,"knowledge rebuild is deterministic");
    const auto repeated = roads.path(rebuilt,{0,0,0},roads.nodes()[8].position);
    require(repeated.nodes==route.nodes && repeated.edges==route.edges,"path is deterministic");
    close(repeated.lengthCm,route.lengthCm,"deterministic route length");
    }

  auto alteredNodes = nodes();
  auto alteredEdges = edges();
  alteredNodes[8].position = {0,0,1};
  alteredNodes[9].position = roads.nodes()[8].position;
  alteredEdges[8] = {0,5,1,false}; // unknown edge would become an enormous shortcut
  alteredEdges[9] = {0,9,1,false};
  const Roads altered(std::move(alteredNodes),std::move(alteredEdges));
  const auto blind = altered.path(reference,{0,0,0},roads.nodes()[8].position);
  require(blind.nodes==route.nodes && blind.edges==route.edges && blind.goalInKnown==route.goalInKnown,
          "changing unknown coordinates/topology cannot influence a familiar-road query");
  close(blind.lengthCm,route.lengthCm,"unknown edge length cannot affect route length");
  close(blind.remainingCm,route.remainingCm,"unknown coordinates cannot affect remainder");
  close(blind.direction.x,route.direction.x,"unknown coordinates cannot affect direction x");
  close(blind.direction.z,route.direction.z,"unknown coordinates cannot affect direction z");
  const Roads empty({},{});
  const auto unknown = empty.path(empty.build({},{0,0,0}),{0,0,0},{300,0,400});
  require(unknown.nodes.empty() && unknown.edges.empty() && !unknown.goalInKnown,"empty knowledge has no invented route");
  close(unknown.remainingCm,500,"empty knowledge leaves entire straight remainder");
  std::puts("daily cycle/determinism: 16 rebuilds identical; unknown topology/coordinates do not influence query; empty graph handled");
  }

void roadClassificationSeam() {
  const Roads roads({{"a",{0,0,0}},{"b",{10000,0,0}},
                     {"c",{11000,0,0}},{"d",{21000,0,0}}},
                    {{0,1,10000,true},{1,2,1000,false},{2,3,10000,true}});
  const auto stranger = roads.build({},{0,0,0});
  require(stranger.edges[0] && stranger.edges[2],"300 m network radius includes road across a classification seam");
  require(!stranger.edges[1],"knowing a nearby road does not teach the non-major seam");
  const auto path = roads.path(stranger,{0,0,0},{21000,0,0});
  familiarEdgesOnly(roads,stranger,path);
  require(path.nodes.back()==1 && path.goalInKnown,"familiar goal across unknown classification seam stays unreachable");
  close(path.remainingCm,11000,"classification seam leaves correct remainder");
  std::puts("road classification: nearby major fragments known; unknown minor seam remains uncrossable");
  }
}

int main() {
  localAndStranger();
  unknownGoalAndAdd();
  acquisitionAndDeterminism();
  roadClassificationSeam();
  std::puts("navigation roads contracts passed");
  }
