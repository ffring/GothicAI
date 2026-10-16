#include "physics/navmesh.h"

#include <chrono>
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
    mesh.buildOrLoad(geometry,params,cache,"contract");
    check(mesh.stats().complete && !mesh.stats().fromCache,"first build must build complete mesh");
    const auto detour=mesh.findPath({3,0,10},{17,0,10});
    check(detour.complete && detour.corners.size()>=4 && detour.length>18.f,"wall must require a real multi-tile detour");
    check(!mesh.findPath({3,0,10},{35,0,5}).complete,"disconnected island must reject partial paths");
    check(!mesh.findPath({100,0,100},{17,0,10}).complete,"off-mesh start must reject path");
    check(mesh.stats().cacheBytes>0 && mesh.stats().tiles>1,"tiled cache must be populated");
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
    mesh.writePolygons(cache/"polygons.jsonl");
    check(std::filesystem::file_size(cache/"polygons.jsonl")>0,"polygon plot export must be nonempty");
    std::filesystem::remove_all(cache);
    std::cout << "navmesh contract: detour, disconnected/off-mesh rejection, tiled cache, invalidation, corruption, deep/shallow water, cave, export passed\n";
    return 0;
  } catch(const std::exception& error) {
    std::filesystem::remove_all(cache);
    std::cerr << "navmesh contract: " << error.what() << '\n';
    return 1;
    }
  }
