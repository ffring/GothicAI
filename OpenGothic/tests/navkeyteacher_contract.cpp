#include "navkeyteacher.h"

#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
using Point = NavMesh::Point;
void check(bool condition,const char* message) {
  if(!condition) throw std::runtime_error(message);
  }
NavMesh::Path route(std::initializer_list<Point> corners) {
  NavMesh::Path p;
  p.complete=true;
  p.corners=corners;
  return p;
  }
void follow(NavKeyTeacher::State body,const NavMesh::Path& path,const Point& goal) {
  for(unsigned tick=0;tick<1600;++tick) {
    const auto decision=NavKeyTeacher::decide(body,path,goal);
    if(decision.arrived) return;
    body.yawDegrees-=decision.keys.turn*9.f;
    const float radians=body.yawDegrees*3.14159265358979323846f/180.f;
    const float speed=decision.keys.forward*(decision.keys.walk?.85f:3.f);
    body.velocity={std::cos(radians)*speed,0,std::sin(radians)*speed};
    body.position[0]+=body.velocity[0]*.1f;
    body.position[2]+=body.velocity[2]*.1f;
    }
  throw std::runtime_error("memoryless keyboard teacher did not finish the little graph");
  }
}

int main() {
  try {
    NavKeyTeacher::State body;
    const auto straight=route({{0,0,0},{10,0,0}});
    auto d=NavKeyTeacher::decide(body,straight,{10,0,0});
    check(d.pathValid && d.keys.walk && d.keys.forward==1 && d.keys.turn==0,
          "ordinary route must use forward walking");
    body.yawDegrees=90;
    d=NavKeyTeacher::decide(body,straight,{10,0,0});
    check(d.keys.turn==1 && d.keys.forward==0,"positive turn must reduce NPC yaw");
    body.yawDegrees=-90;
    check(NavKeyTeacher::decide(body,straight,{10,0,0}).keys.turn==-1,
          "negative turn must increase NPC yaw");
    body.yawDegrees=179;
    const auto wrap=route({{0,0,0},{-10,0,-.1f}});
    check(NavKeyTeacher::decide(body,wrap,wrap.corners.back()).keys.turn==0,
          "heading wrap must use the shortest rotation with a digital deadband");

    const auto elbow=route({{0,0,0},{4,0,0},{4,0,5},{9,0,5}});
    body={};
    follow(body,elbow,elbow.corners.back());
    // The same pure policy labels arbitrary off-route student poses and rotations.
    for(float offset:{-5.f,-2.f,1.f,3.f,5.f}) {
      body={};body.position={2,0,offset};body.yawDegrees=offset*43.f;
      follow(body,elbow,elbow.corners.back());
      }
    body={};body.position={4.1f,0,.15f};body.yawDegrees=90;
    d=NavKeyTeacher::decide(body,elbow,elbow.corners.back());
    check(d.hint.cornerIndex>=2 && d.keys.forward==1,"a passed corner must not pull the body backwards");

    for(const auto type:{NavMesh::LinkType::Jump,NavMesh::LinkType::Climb,NavMesh::LinkType::Drop}) {
      auto linked=route({{0,0,0},{2,0,0},{4,1,0},{7,1,0}});
      NavMesh::Path::Transition link;
      link.start=linked.corners[1];link.end=linked.corners[2];link.type=type;link.id=42;link.cornerIndex=1;
      linked.transitions.push_back(link);
      body={};body.position=link.start;
      d=NavKeyTeacher::decide(body,linked,linked.corners.back());
      check(d.hint.onTransition && d.hint.transitionId==42 && !d.keys.walk && d.keys.forward==1,
            "link takeoff must aim at its directed landing and use running input");
      check(d.keys.jump==(type==NavMesh::LinkType::Jump) && d.keys.climb==(type==NavMesh::LinkType::Climb),
            "jump/climb/drop links must retain their keyboard action");
      if(type==NavMesh::LinkType::Climb) {
        body.standing=false;
        d=NavKeyTeacher::decide(body,linked,linked.corners.back());
        check(d.hint.onTransition && d.keys.forward==0 && !d.keys.jump && !d.keys.climb,
              "moving at a climb takeoff must release forward until the native standing gate opens");
        body.standing=true;
        d=NavKeyTeacher::decide(body,linked,linked.corners.back());
        check(d.keys.forward==1 && d.keys.climb && !d.keys.jump,
              "standing at a climb takeoff must resume forward plus climb without a remembered phase");
        }
      body.yawDegrees=9;
      d=NavKeyTeacher::decide(body,linked,linked.corners.back());
      check(type==NavMesh::LinkType::Drop || (d.keys.forward==0 && !d.keys.jump && !d.keys.climb),
            "jump/climb takeoff must wait for alignment instead of walking off the edge");
      body.yawDegrees=0;
      body.position={3,.5f,0};body.supported=false;body.inAir=true;
      d=NavKeyTeacher::decide(body,linked,linked.corners.back());
      check(d.hint.onTransition && !d.keys.jump && !d.keys.climb && !d.replan,
            "midair state must reconstruct the transition without jump history");
      body={};body.position=link.end;
      d=NavKeyTeacher::decide(body,linked,linked.corners.back());
      check(!d.hint.onTransition && d.hint.cornerIndex>=3 && d.keys.walk,
            "supported landing must advance without historical transition flags");
      }

    body={};body.position={10,0,0};
    check(NavKeyTeacher::decide(body,straight,{10,0,0}).arrived,"goal radius must stop the teacher");
    body={};NavMesh::Path missing;
    d=NavKeyTeacher::decide(body,missing,{10,0,0});
    check(!d.pathValid && d.replan && d.keys.forward==0,"missing path must request replanning without invented movement");
    body.position[0]=std::numeric_limits<float>::quiet_NaN();
    d=NavKeyTeacher::decide(body,straight,{10,0,0});
    check(d.replan && std::isfinite(d.keys.turn),"non-finite body must not create non-finite actions");
    std::cout<<"navkeyteacher contract: digital yaw, walking, little graph, five off-route states, passed corner, jump/climb/drop, climb standing gate, midair reconstruction, landing, invalid input passed\n";
    return 0;
  } catch(const std::exception& error) {
    std::cerr<<"navkeyteacher contract: "<<error.what()<<'\n';
    return 1;
    }
  }
