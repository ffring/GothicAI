#include "physics/navmesh.h"

#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace {
using Point = NavMesh::Point;
void check(bool condition, const char* message) {
  if(!condition) throw std::runtime_error(message);
  }
void triangle(std::vector<float>& vertices, std::vector<int>& indices, Point a, Point b, Point c) {
  const int base=int(vertices.size()/3);
  for(const auto& p:{a,b,c}) vertices.insert(vertices.end(),p.begin(),p.end());
  indices.insert(indices.end(),{base,base+1,base+2});
  }
void floor(NavMesh::Geometry& geometry, float x0, float z0, float x1, float z1, float y=0.f, bool water=false) {
  auto& vertices=water ? geometry.waterVertices : geometry.vertices;
  auto& indices=water ? geometry.waterTriangles : geometry.triangles;
  triangle(vertices,indices,{x0,y,z0},{x0,y,z1},{x1,y,z1});
  triangle(vertices,indices,{x0,y,z0},{x1,y,z1},{x1,y,z0});
  }
void wall(NavMesh::Geometry& geometry) {
  floor(geometry,9.5f,4.f,10.5f,16.f,3.f);
  auto& v=geometry.vertices;
  auto& t=geometry.triangles;
  triangle(v,t,{9.5f,0,4},{9.5f,3,4},{9.5f,3,16});
  triangle(v,t,{9.5f,0,4},{9.5f,3,16},{9.5f,0,16});
  triangle(v,t,{10.5f,0,4},{10.5f,0,16},{10.5f,3,16});
  triangle(v,t,{10.5f,0,4},{10.5f,3,16},{10.5f,3,4});
  triangle(v,t,{9.5f,0,4},{10.5f,0,4},{10.5f,3,4});
  triangle(v,t,{9.5f,0,4},{10.5f,3,4},{9.5f,3,4});
  triangle(v,t,{9.5f,0,16},{9.5f,3,16},{10.5f,3,16});
  triangle(v,t,{9.5f,0,16},{10.5f,3,16},{10.5f,0,16});
  }
}

int main() {
  const auto cache=std::filesystem::temp_directory_path()/
                   ("gothic-navmesh-contract-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
  try {
    NavMesh::Params params;
    params.tileSize=32;
    NavMesh::Geometry geometry;
    floor(geometry,0,0,20,20);
    floor(geometry,30,0,40,10);
    wall(geometry);
    NavMesh mesh;
    check(!mesh.straightWalkable({3,0,10},{17,0,10}),"unbuilt mesh must not authorize direct walking");
    mesh.buildOrLoad(geometry,params,cache,"contract");
    check(mesh.stats().complete && !mesh.stats().fromCache,"first build must build complete mesh");
    const auto detour=mesh.findPath({3,0,10},{17,0,10});
    check(detour.complete && detour.corners.size()>=4 && detour.length>18.f,"wall must require a real multi-tile detour");
    check(!mesh.straightWalkable({3,0,10},{17,0,10}),"wall must block a direct ground ray despite a complete detour");
    check(mesh.straightWalkable({3,0,2},{17,0,2}),"clear multi-tile ground must permit direct steering");
    check(!mesh.straightWalkable({3,0,10},{35,0,5}),"disconnected ground must not authorize direct walking");
    check(!mesh.straightWalkable({100,0,100},{17,0,10}),"off-mesh endpoint must not authorize direct walking");
    check(!mesh.findPath({3,0,10},{35,0,5}).complete,"disconnected island must reject partial paths");
    check(!mesh.findPath({100,0,100},{17,0,10}).complete,"off-mesh start must reject path");
    check(mesh.stats().cacheBytes>0 && mesh.stats().tiles>1,"tiled cache must be populated");
    const auto shortcuts=mesh.shortcutCandidates(128);
    check(!shortcuts.candidates.empty(),"whole-world boundaries must expose shortcut probe sites");
    bool connectedShortcut=false;
    for(const auto& candidate:shortcuts.candidates) {
      const float direct=std::sqrt(std::pow(candidate.start[0]-candidate.end[0],2.f)+
                                   std::pow(candidate.start[1]-candidate.end[1],2.f)+
                                   std::pow(candidate.start[2]-candidate.end[2],2.f));
      check(candidate.horizontalDistance<=4.f && candidate.verticalDistance<=3.f,
            "shortcut endpoints must respect physical probe bounds after insetting");
      if(candidate.groundComplete) {
        connectedShortcut=true;
        check(candidate.groundDistance>=3.f*direct && candidate.groundDistance>=direct+10.f,
              "connected shortcut must save at least 10m and two thirds of ground distance");
        }
      }
    check(connectedShortcut,"same connected ground component around a wall must produce a real shortcut");
    check(shortcuts.candidates.size()<=128 && shortcuts.groundQueries<=shortcuts.queryBudget && shortcuts.queryBudget==1280,
          "shortcut sites and ground-query work must obey explicit budgets");
    const auto repeatedShortcuts=mesh.shortcutCandidates(128);
    check(repeatedShortcuts.candidates.size()==shortcuts.candidates.size() &&
          repeatedShortcuts.groundQueries==shortcuts.groundQueries,"shortcut search must be repeatable");
    for(size_t i=0; i<shortcuts.candidates.size(); ++i)
      check(shortcuts.candidates[i].start==repeatedShortcuts.candidates[i].start &&
            shortcuts.candidates[i].end==repeatedShortcuts.candidates[i].end,
            "shortcut budget ordering must be deterministic");
    NavMesh loaded;
    loaded.buildOrLoad(geometry,params,cache,"contract");
    check(loaded.stats().fromCache,"second build must load cache");
    check(loaded.findPath({3,0,10},{17,0,10}).complete,"cached mesh must retain tile connectivity");
    check(loaded.stats().key==mesh.stats().key,"cache key must be stable");
    auto changed=params;
    changed.radius+=0.1f;
    NavMesh resized;
    resized.buildOrLoad(geometry,changed,cache,"contract");
    check(!resized.stats().fromCache && resized.stats().key!=mesh.stats().key,"agent dimensions must invalidate cache");
    auto modified=geometry;
    modified.vertices[0]+=0.1f;
    NavMesh edited;
    edited.buildOrLoad(modified,params,cache,"contract");
    check(!edited.stats().fromCache && edited.stats().key!=mesh.stats().key,"collision world must invalidate cache");
    // Detect corruption before giving native Detour a cached tile's bytes.
    {
      std::fstream file(mesh.stats().cacheFile,std::ios::binary|std::ios::in|std::ios::out);
      file.seekp(-1,std::ios::end);
      file.put(char(0x7f));
    }
    NavMesh repaired;
    repaired.buildOrLoad(geometry,params,cache,"contract");
    check(!repaired.stats().fromCache && repaired.findPath({3,0,10},{17,0,10}).complete,"corrupt cache must rebuild safely");
    NavMesh::Geometry basin;
    floor(basin,0,0,20,20);
    floor(basin,8,0,12,20,2.f,true);
    NavMesh flooded;
    flooded.buildOrLoad(basin,params,cache,"water");
    check(!flooded.findPath({3,0,10},{17,0,10}).complete,"deep water must block bottom routes");
    for(size_t i=1; i<basin.waterVertices.size(); i+=3) basin.waterVertices[i]=0.25f;
    NavMesh shallow;
    shallow.buildOrLoad(basin,params,cache,"shallow");
    check(shallow.findPath({3,0,10},{17,0,10}).complete,"shallow water must retain walkable ground");
    for(size_t i=1; i<basin.waterVertices.size(); i+=3) basin.waterVertices[i]=2.f;
    // A cave ceiling occludes water above it, matching the physics water ray.
    floor(basin,0,0,20,20,1.5f);
    NavMesh cave;
    cave.buildOrLoad(basin,params,cache,"cave");
    check(cave.findPath({3,0,10},{17,0,10}).complete,"water above a solid ceiling must not flood cave floor");
    NavMesh::Geometry floors;
    floor(floors,0,0,20,20);
    floor(floors,8,0,18,20,3.f);
    NavMesh layered;
    layered.buildOrLoad(floors,params,cache,"floors");
    check(layered.straightWalkable({3,0,10},{12,0,10}),"direct walking must remain on the ground under a balcony");
    check(!layered.straightWalkable({3,0,10},{12,3,10}),"2D ray success must not confuse a balcony with the ground below");
    NavMesh::Geometry gap;
    floor(gap,0,0,8,8);
    floor(gap,10,0,18,8);
    NavMesh separated;
    separated.buildOrLoad(gap,params,cache,"gap");
    const Point takeoff={3,0,4}, landing={15,0,4};
    check(!separated.findPath(takeoff,landing).complete,"unproven gap must stay disconnected");
    const auto candidates=separated.gapCandidates(takeoff,landing);
    check(!candidates.empty() && candidates.front().horizontalDistance<=4.f,"disconnected components must expose nearby boundary probe sites");
    check(separated.gapCandidates(takeoff,landing,1.f).empty(),"boundary search must respect jump distance bound");
    check(separated.gapCandidates(takeoff,{4,0,4}).empty(),"connected ground must not produce gap candidates");
    auto linkedParams=params;
    NavMesh::OffMeshLink link;
    link.start=candidates.front().start;
    link.end=candidates.front().end;
    link.type=NavMesh::LinkType::Jump;
    link.id=42;
    linkedParams.links.push_back(link);
    NavMesh linked;
    linked.buildOrLoad(gap,linkedParams,cache,"gap");
    check(linked.stats().key!=separated.stats().key,"proven transition must invalidate mesh cache");
    const auto crossed=linked.findPath(takeoff,landing);
    check(crossed.complete && crossed.transitions.size()==1,"proven jump must connect islands");
    check(!linked.straightWalkable(takeoff,landing),"an off-mesh jump must never authorize direct ground steering");
    check(!linked.findPath(takeoff,landing,false).complete,
          "ground-only baseline must remain disconnected even when a proven jump joins the islands");
    const auto& transition=crossed.transitions.front();
    check(transition.type==NavMesh::LinkType::Jump && transition.id==42,"path must retain transition kind and identity");
    check(transition.cornerIndex+1<crossed.corners.size() &&
          crossed.corners[transition.cornerIndex]==transition.start &&
          crossed.corners[transition.cornerIndex+1]==transition.end,"path must expose both directed transition endpoints as corners");
    check(!linked.findPath(landing,takeoff).complete,"one successful probe must not authorize reverse traversal");
    NavMesh linkedLoaded;
    linkedLoaded.buildOrLoad(gap,linkedParams,cache,"gap");
    const auto cachedCrossing=linkedLoaded.findPath(takeoff,landing);
    check(linkedLoaded.stats().fromCache && cachedCrossing.complete && cachedCrossing.transitions.size()==1 &&
          cachedCrossing.transitions.front().type==NavMesh::LinkType::Jump &&
          cachedCrossing.transitions.front().id==42,"cached transition must retain its connectivity, type and identity");
    check(!linkedLoaded.findPath(takeoff,landing,false).complete,
          "ground-only baseline must exclude off-mesh links from preexisting caches too");
    linkedParams.links.front().type=NavMesh::LinkType::Climb;
    linkedParams.links.front().bidirectional=true;
    NavMesh climb;
    climb.buildOrLoad(gap,linkedParams,cache,"gap");
    const auto reverseClimb=climb.findPath(landing,takeoff);
    check(climb.stats().key!=linked.stats().key && reverseClimb.complete &&
          reverseClimb.transitions.size()==1 && reverseClimb.transitions.front().type==NavMesh::LinkType::Climb,
          "explicitly verified reverse climb must be typed and cached separately");
    linkedParams.links.front().type=NavMesh::LinkType::Drop;
    NavMesh drop;
    drop.buildOrLoad(gap,linkedParams,cache,"gap");
    const auto dropped=drop.findPath(takeoff,landing);
    check(drop.stats().key!=climb.stats().key && dropped.complete &&
          dropped.transitions.front().type==NavMesh::LinkType::Drop,"drop kind must survive mesh construction and change cache key");
    mesh.writePolygons(cache/"polygons.jsonl");
    check(std::filesystem::file_size(cache/"polygons.jsonl")>0,"polygon plot export must be nonempty");
    std::filesystem::remove_all(cache);
    std::cout << "navmesh contract: detour, direct-ground rays, layered-floor rejection, disconnected/off-mesh rejection, tiled cache, invalidation, corruption, deep/shallow water, cave, gap boundaries, directed jump/climb/drop links, export passed\n";
    return 0;
  } catch(const std::exception& error) {
    std::filesystem::remove_all(cache);
    std::cerr << "navmesh contract: " << error.what() << '\n';
    return 1;
    }
  }
