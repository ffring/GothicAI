#pragma once

#include <Tempest/Vec>
#include <zenkit/Mesh.hh>
#include <zenkit/world/BspTree.hh>
#include <algorithm>
#include <array>
#include <cmath>
#include <set>
#include <vector>

namespace PortalRooms {

// BSP leaves can extend from an indoor floor to the sky outside a tower or
// ship. Room surfaces (is_sector) and portal caps bound the actual room volume;
// ordinary landscape polygons such as a ship's yard are not room ceilings.
class Volumes final {
  public:
    void reset(const zenkit::Mesh& mesh,const zenkit::BspTree& bsp) {
      indoorWorld=bsp.mode==zenkit::BspTreeType::INDOOR;
      rooms.clear();
      for(const auto& sector:bsp.sectors) {
        std::set<uint32_t> polygons;
        for(const auto leaf:sector.node_indices) {
          if(leaf>=bsp.leaf_node_indices.size()) continue;
          const auto node=bsp.leaf_node_indices[leaf];
          if(node>=bsp.nodes.size()) continue;
          const auto& bounds=bsp.nodes[node];
          const size_t end=std::min(size_t(bounds.polygon_index)+bounds.polygon_count,bsp.polygon_indices.size());
          for(size_t i=bounds.polygon_index;i<end;++i) {
            const auto polygon=bsp.polygon_indices[i];
            if(polygon<mesh.geometry.size() && mesh.geometry[polygon].flags.is_sector)
              polygons.insert(polygon);
            }
          }
        // Portal polygons are invisible openings in the renderer, but close
        // the indoor volume at doors and windows for this containment test.
        polygons.insert(sector.portal_polygon_indices.begin(),sector.portal_polygon_indices.end());
        Room room;
        for(const auto index:polygons) {
          if(index>=mesh.geometry.size()) continue;
          const auto& polygon=mesh.geometry[index];
          if(polygon.index_count<3 || polygon.index_offset+polygon.index_count>mesh.polygon_vertex_indices.size()) continue;
          const auto source=[&](size_t corner) { return mesh.polygon_vertex_indices[polygon.index_offset+corner]; };
          for(size_t corner=2;corner<polygon.index_count;++corner) {
            const auto a=source(0),b=source(corner-1),c=source(corner);
            if(a>=mesh.vertices.size() || b>=mesh.vertices.size() || c>=mesh.vertices.size()) continue;
            Triangle triangle;
            const uint32_t indices[3]={a,b,c};
            for(size_t i=0;i<3;++i) {
              const auto point=mesh.vertices[indices[i]];
              triangle.point[i]={point.x,point.y,point.z};
              if(room.triangles.empty() && i==0) room.min=room.max=triangle.point[i];
              room.min.x=std::min(room.min.x,point.x); room.max.x=std::max(room.max.x,point.x);
              room.min.y=std::min(room.min.y,point.y); room.max.y=std::max(room.max.y,point.y);
              room.min.z=std::min(room.min.z,point.z); room.max.z=std::max(room.max.z,point.z);
              }
            room.triangles.push_back(triangle);
            }
          }
        if(!room.triangles.empty()) rooms.push_back(std::move(room));
        }
      }

    bool isIndoor(const Tempest::Vec3& point) const {
      if(indoorWorld) return true;
      for(const auto& room:rooms) {
        if(point.x<room.min.x || point.x>room.max.x ||
           point.y<room.min.y || point.y>room.max.y ||
           point.z<room.min.z || point.z>room.max.z) continue;
        // Three oblique rays avoid an edge or coincident portal changing the
        // answer. Coincident intersections count once, including back faces.
        const std::array<Tempest::Vec3,3> directions={Tempest::Vec3(.371f,.529f,.763f),
                                                   Tempest::Vec3(-.683f,.413f,.601f),
                                                   Tempest::Vec3(.227f,.851f,-.473f)};
        unsigned inside=0;
        for(const auto& direction:directions) {
          std::vector<double> intersections;
          for(const auto& triangle:room.triangles) {
            const double distance=intersect(point,direction,triangle);
            if(distance>=0.) intersections.push_back(distance);
            }
          std::sort(intersections.begin(),intersections.end());
          size_t crossings=0;
          double previous=-1.;
          for(const auto distance:intersections) {
            if(previous<0. || distance-previous>.01) ++crossings;
            previous=distance;
            }
          inside+=crossings%2;
          }
        if(inside>=2) return true;
        }
      return false;
      }

  private:
    struct Triangle { std::array<Tempest::Vec3,3> point; };
    struct Room {
      Tempest::Vec3 min,max;
      std::vector<Triangle> triangles;
      };
    bool indoorWorld=false;
    std::vector<Room> rooms;

    static double intersect(const Tempest::Vec3& from,const Tempest::Vec3& direction,const Triangle& triangle) {
      struct Double3 {
        double x,y,z;
        explicit Double3(const Tempest::Vec3& v):x(v.x),y(v.y),z(v.z) {}
        Double3(double x,double y,double z):x(x),y(y),z(z) {}
        Double3 operator-(const Double3& b) const { return {x-b.x,y-b.y,z-b.z}; }
        double dot(const Double3& b) const { return x*b.x+y*b.y+z*b.z; }
        Double3 cross(const Double3& b) const { return {y*b.z-z*b.y,z*b.x-x*b.z,x*b.y-y*b.x}; }
        };
      const Double3 start(from),ray(direction),a(triangle.point[0]),b(triangle.point[1]),c(triangle.point[2]);
      const auto edge1=b-a,edge2=c-a,p=ray.cross(edge2);
      const double determinant=edge1.dot(p);
      if(std::abs(determinant)<1.e-9) return -1.;
      const auto offset=start-a;
      const double u=offset.dot(p)/determinant;
      if(u<-.0000001 || u>1.0000001) return -1.;
      const auto q=offset.cross(edge1);
      const double v=ray.dot(q)/determinant;
      if(v<-.0000001 || u+v>1.0000001) return -1.;
      const double distance=edge2.dot(q)/determinant;
      return distance>=0. ? distance : -1.;
      }
  };
}
