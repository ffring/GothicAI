#include "game/navigationmemory.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace {
using Tempest::Vec3;
using Memory = NavigationMemory;
using Ray = NavigationEye::Ray;
using Kind = NavigationEye::HitKind;
using Field = float Memory::Cell::*;

void require(bool condition, const char* message) {
  if(condition)
    return;
  std::fprintf(stderr,"FAIL: %s\n",message);
  std::exit(EXIT_FAILURE);
  }

void close(float actual, float expected, float tolerance, const char* message) {
  require(std::isfinite(actual) && std::abs(actual-expected)<=tolerance,message);
  }

Ray ray(const Vec3& point, Kind kind = Kind::World, float normalY = 0, uint64_t time = 0,
        const Vec3& origin = Vec3(0,150,0)) {
  Ray result;
  result.origin = origin;
  result.point = point;
  result.direction = Vec3::normalize(point-origin);
  result.distanceCm = (point-origin).length();
  result.maxRangeCm = result.distanceCm;
  result.sampledAtMs = result.nearSampledAtMs = time;
  result.kind = kind;
  result.normalY = normalY;
  result.hit = kind!=Kind::None;
  result.complete = true;
  return result;
  }

struct Found final { float right = 0, forward = 0, confidence = 0; };

Found strongest(const Memory::Layer& layer, Field field) {
  Found result;
  for(size_t z=0; z<layer.size; ++z)
    for(size_t x=0; x<layer.size; ++x) {
      const float value = layer.cells[z*layer.size+x].*field;
      if(value>result.confidence)
        result = {(float(x)+.5f-float(layer.size)/2.f)*layer.cellCm,
                  (float(z)+.5f-float(layer.size)/2.f)*layer.cellCm,value};
      }
  return result;
  }

float around(const Memory::Layer& layer, float right, float forward, Field field) {
  float result = 0;
  for(size_t z=0; z<layer.size; ++z)
    for(size_t x=0; x<layer.size; ++x) {
      const float dx = (float(x)+.5f-float(layer.size)/2.f)*layer.cellCm-right;
      const float dz = (float(z)+.5f-float(layer.size)/2.f)*layer.cellCm-forward;
      if(std::abs(dx)<=layer.cellCm && std::abs(dz)<=layer.cellCm)
        result = std::max(result,layer.cells[z*layer.size+x].*field);
      }
  return result;
  }

void turnHonestyAndFreeSpace() {
  Memory memory;
  Memory::View view;
  const std::vector<Ray> seen = {ray({500,150,0})};
  memory.update(seen,{{0,0,0},{1,0,0},0});
  memory.view(view);
  auto stone = strongest(view.near,&Memory::Cell::wall);
  close(stone.forward,500,25,"stone five metres in front");
  close(stone.right,0,25,"stone lateral position");
  for(float distance=50; distance<475; distance+=25)
    require(around(view.near,0,distance,&Memory::Cell::free)>.99f,"cells before a ray hit are free");
  require(around(view.near,150,300,&Memory::Cell::confidence)==0,"unseen obstacle and between-ray area remain unknown");
  memory.update(seen,{{0,0,0},{-1,0,0},1000});
  memory.view(view);
  stone = strongest(view.near,&Memory::Cell::wall);
  close(stone.forward,-500,25,"turning 180 degrees keeps stone five metres behind");
  close(stone.right,0,25,"turn preserves stone lateral position");
  std::printf("turn: stone behind %.1f cm, confidence %.6f; unseen=0, ray cells free\n",stone.forward,stone.confidence);
  }

void timeAndDistanceDecay() {
  Memory memory;
  Memory::View view;
  const std::vector<Ray> seen = {ray({5000,150,0}),ray({5000,150,600},Kind::MovableVob)};
  memory.update(seen,{{0,0,0},{1,0,0},0});
  memory.view(view);
  const float firstStatic = strongest(view.far,&Memory::Cell::wall).confidence;
  const float firstMovable = strongest(view.far,&Memory::Cell::movable).confidence;
  memory.update(seen,{{0,0,0},{1,0,0},5000});
  memory.view(view);
  const float timedStatic = strongest(view.far,&Memory::Cell::wall).confidence;
  const float timedMovable = strongest(view.far,&Memory::Cell::movable).confidence;
  require(timedStatic<firstStatic && timedMovable<firstMovable && timedMovable<timedStatic,
          "time decay is monotonic, movable objects fade faster");
  float previous = timedStatic;
  for(int metres=10; metres<=30; metres+=10) {
    memory.update(seen,{{float(metres)*100.f,0,0},{1,0,0},5000});
    memory.view(view);
    const float current = strongest(view.far,&Memory::Cell::wall).confidence;
    require(current<previous,"travelled path decay is monotonic");
    previous = current;
    }
  const auto stone = strongest(view.far,&Memory::Cell::wall);
  close(stone.forward,2000,200,"after thirty metres the fifty-metre object remains at the right relative position");
  close(stone.confidence,float(std::exp(-5./30.-1.)),1.e-5f,"time and path confidence formula");
  std::printf("decay: static %.6f -> %.6f, movable %.6f -> %.6f; after 30 m %.6f at %.1f cm\n",
              firstStatic,timedStatic,firstMovable,timedMovable,stone.confidence,stone.forward);
  }

void cachedRaysAndIncompleteRays() {
  Memory memory;
  Memory::View view;
  auto cached = ray({5000,150,0});
  memory.update({cached},{{0,0,0},{1,0,0},0});
  cached.nearSampledAtMs = 1000;
  memory.update({cached},{{3000,0,0},{-1,0,0},1000});
  memory.view(view);
  auto stone = strongest(view.far,&Memory::Cell::wall);
  close(stone.forward,-2000,200,"cached far ray keeps its world pose");
  close(stone.confidence,float(std::exp(-1./30.-1.)),1.e-5f,"cached far ray never refreshes its timestamp or path");
  auto freshNear = ray({3250,150,0},Kind::World,0,1100,{3000,150,0});
  memory.update({freshNear},{{3000,0,0},{-1,0,0},1100});
  memory.update({cached},{{3000,0,0},{-1,0,0},2000});
  memory.view(view);
  close(around(view.far,0,-2000,&Memory::Cell::wall),float(std::exp(-2./30.-1.)),1.e-5f,
        "an older far cache returning after a near hit is not ingested again");

  Memory partial;
  auto incomplete = ray({500,150,0},Kind::None);
  incomplete.complete = false;
  incomplete.maxRangeCm = 10000;
  partial.update({incomplete},{{0,0,0},{1,0,0},0});
  partial.view(view);
  require(around(view.near,0,400,&Memory::Cell::free)>.99f,"incomplete ray's observed near segment is free");
  require(around(view.near,0,650,&Memory::Cell::confidence)==0,"incomplete ray does not invent the uncast far segment");
  std::puts("cache: timestamp/pose retained, stale cache after new hit skipped; partial near segment retained");
  }

void groundAndLowEmptyAir() {
  Memory memory;
  Memory::View view;
  const std::vector<Ray> ground = {ray({300,80,100},Kind::World,1.f)};
  memory.update(ground,{{0,0,0},{1,0,0},0});
  memory.view(view);
  require(strongest(view.near,&Memory::Cell::ledge).confidence>.99f,"ground eighty centimetres above feet is a ledge");
  memory.update(ground,{{0,100,0},{1,0,0},1000});
  memory.view(view);
  require(strongest(view.near,&Memory::Cell::ledge).confidence==0,"ledge is recomputed relative to current feet");
  require(around(view.near,100,300,&Memory::Cell::free)>0,"ground near current feet is free");
  memory.update(ground,{{0,200,0},{1,0,0},2000});
  memory.view(view);
  require(strongest(view.near,&Memory::Cell::drop).confidence>0,"remembered ground below current feet is a drop");

  Memory hole;
  hole.update({ray({500,-200,0},Kind::None)},{{0,0,0},{1,0,0},0});
  hole.view(view);
  require(around(view.near,0,450,&Memory::Cell::drop)>.99f,"ray through air below expected ground proves a drop");

  Memory vertical;
  vertical.update({ray({500,0,0},Kind::World,1.f),ray({500,150,100},Kind::World)},{{0,0,0},{1,0,0},0});
  vertical.update({ray({700,300,0},Kind::None,0,1000,{0,300,0}),
                   ray({700,300,140},Kind::None,0,1000,{0,300,0})},{{0,0,0},{1,0,0},1000});
  vertical.view(view);
  require(around(view.near,0,500,&Memory::Cell::free)>0,"high empty ray retains ground");
  require(around(view.near,100,500,&Memory::Cell::wall)>0,"high empty ray does not erase wall below it");
  vertical.update({ray({700,150,140},Kind::None,0,2000)},{{0,0,0},{1,0,0},2000});
  vertical.view(view);
  require(around(view.near,100,500,&Memory::Cell::wall)==0,"new empty ray through recorded wall height clears that wall");
  std::puts("ground: ledge/free/drop follow current feet; low empty air proves drop; high air preserves lower surfaces");
  }

void windowAndTravelHistory() {
  Memory memory;
  Memory::View view;
  memory.update({ray({5000,150,0})},{{0,0,0},{1,0,0},0});
  // Distance must include the entire path, not just displacement or eye ticks.
  memory.update({},{{1000,0,0},{1,0,0},1000});
  memory.update({},{{0,0,0},{1,0,0},2000});
  memory.update({ray({5000,150,0})},{{0,0,0},{1,0,0},3000});
  memory.view(view);
  close(float(view.traveledCm),2000,1.e-4f,"empty observation ticks accumulate path");
  close(strongest(view.far,&Memory::Cell::wall).confidence,float(std::exp(-3./30.-2./3.)),1.e-5f,
        "returning to the same place does not restore confidence");
  memory.update({},{{20000,0,0},{1,0,0},4000});
  memory.update({},{{0,0,0},{1,0,0},5000});
  memory.view(view);
  require(strongest(view.far,&Memory::Cell::wall).confidence==0,"evicted world cells never reappear on returning");

  Memory late;
  late.update({},{{0,0,0},{1,0,0},0});
  late.update({},{{3000,0,0},{1,0,0},1000});
  late.update({ray({5000,150,0})},{{3000,0,0},{1,0,0},1000});
  late.view(view);
  close(strongest(view.far,&Memory::Cell::wall).confidence,float(std::exp(-1./30.-1.)),1.e-5f,
        "first ingestion of a cached observation uses its original path timestamp");
  std::puts("windows: world positions survive movement; eviction permanent; per-tick path and late cached observation correct");
  }

void farCellClearingNeedsActualIntersection() {
  Memory memory;
  Memory::View view;
  // Both projected lines cross the same 200 cm far cell at x=5000,z=0.
  // The second passes 150 cm beside the hit, at exactly the same height.
  memory.update({ray({5100,150,25},Kind::World,0,0,{0,150,25})},{{0,0,0},{1,0,0},0});
  memory.update({ray({5500,150,175},Kind::None,0,1000,{0,150,175})},{{0,0,0},{1,0,0},1000});
  memory.view(view);
  close(around(view.far,25,5100,&Memory::Cell::wall),float(std::exp(-1./30.)),1.e-5f,
        "parallel free ray 150 cm away in the same far cell does not erase a wall hit");
  memory.update({ray({5500,150,25},Kind::None,0,2000,{0,150,25})},{{0,0,0},{1,0,0},2000});
  memory.view(view);
  require(strongest(view.far,&Memory::Cell::wall).confidence==0,
          "free ray through the actual recorded hit clears it even in a far cell");
  std::puts("far clearing: parallel ray 150 cm away retains hit; ray through actual 3D hit clears it");
  }
}

int main() {
  turnHonestyAndFreeSpace();
  timeAndDistanceDecay();
  cachedRaysAndIncompleteRays();
  groundAndLowEmptyAir();
  windowAndTravelHistory();
  farCellClearingNeedsActualIntersection();
  std::puts("navigation memory contracts passed");
  }
