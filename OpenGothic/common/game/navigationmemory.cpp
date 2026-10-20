#include "navigationmemory.h"

#include <algorithm>
#include <cmath>

namespace {
using Tempest::Vec3;

int cellCoordinate(float value, float cellCm) {
  return int(std::floor(value/cellCm));
  }

size_t ringCoordinate(int value, size_t size) {
  const int n = int(size), remainder = value%n;
  return size_t(remainder<0 ? remainder+n : remainder);
  }

bool finite(const Vec3& p) {
  return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
  }
}

NavigationMemory::Grid::Grid(size_t n, float cell):size(n),cellCm(cell),cells(n*n) {
  }

bool NavigationMemory::Grid::contains(int x, int z) const {
  return x>=minX && z>=minZ && x<minX+int(size) && z<minZ+int(size);
  }

NavigationMemory::WorldCell& NavigationMemory::Grid::at(int x, int z) {
  return cells[ringCoordinate(z,size)*size+ringCoordinate(x,size)];
  }

const NavigationMemory::WorldCell& NavigationMemory::Grid::at(int x, int z) const {
  return cells[ringCoordinate(z,size)*size+ringCoordinate(x,size)];
  }

void NavigationMemory::Grid::center(const Vec3& position) {
  const int nextX = cellCoordinate(position.x,cellCm)-int(size/2);
  const int nextZ = cellCoordinate(position.z,cellCm)-int(size/2);
  if(positioned && nextX==minX && nextZ==minZ)
    return;
  if(!positioned || std::abs(nextX-minX)>=int(size) || std::abs(nextZ-minZ)>=int(size)) {
    std::fill(cells.begin(),cells.end(),WorldCell{});
    }
  else {
    // The ring slots in the entering strips belonged to the leaving strips.
    // Clear them now, so walking away and back cannot resurrect evicted data.
    for(int x=nextX; x<nextX+int(size); ++x)
      if(x<minX || x>=minX+int(size))
        for(int z=nextZ; z<nextZ+int(size); ++z)
          at(x,z) = WorldCell{};
    for(int z=nextZ; z<nextZ+int(size); ++z)
      if(z<minZ || z>=minZ+int(size))
        for(int x=nextX; x<nextX+int(size); ++x)
          at(x,z) = WorldCell{};
    }
  minX = nextX;
  minZ = nextZ;
  positioned = true;
  }

NavigationMemory::NavigationMemory():nearGrid(Constants::nearSize,Constants::nearCellCm),
                                    farGrid(Constants::farSize,Constants::farCellCm) {
  pathHistory.reserve(64);
  }

void NavigationMemory::reset() {
  std::fill(nearGrid.cells.begin(),nearGrid.cells.end(),WorldCell{});
  std::fill(farGrid.cells.begin(),farGrid.cells.end(),WorldCell{});
  nearGrid.positioned = farGrid.positioned = false;
  body = Pose{};
  pathCm = 0;
  posed = false;
  rayStamps.clear();
  pathHistory.clear();
  }

float NavigationMemory::pathAt(uint64_t gameMs) const {
  const auto next = std::lower_bound(pathHistory.begin(),pathHistory.end(),gameMs,
                                   [](const PathStamp& stamp, uint64_t time) { return stamp.gameMs<time; });
  if(next==pathHistory.end())
    return float(pathCm);
  if(next==pathHistory.begin() || next->gameMs==gameMs)
    return float(next->pathCm);
  const auto& previous = *(next-1);
  const double fraction = double(gameMs-previous.gameMs)/double(next->gameMs-previous.gameMs);
  return float(previous.pathCm+(next->pathCm-previous.pathCm)*fraction);
  }

void NavigationMemory::update(const std::vector<NavigationEye::Ray>& rays, const Pose& pose) {
  if(!finite(pose.position) || !finite(pose.forward))
    return;
  if(posed && pose.gameMs<body.gameMs)
    reset();
  if(posed)
    pathCm += double((pose.position-body.position).length());
  body = pose;
  const float forwardLength = std::sqrt(body.forward.x*body.forward.x+body.forward.z*body.forward.z);
  body.forward = forwardLength>1.e-6f ? Vec3(body.forward.x/forwardLength,0,body.forward.z/forwardLength) : Vec3(1,0,0);
  posed = true;
  if(!pathHistory.empty() && pathHistory.back().gameMs==pose.gameMs)
    pathHistory.back().pathCm = pathCm;
  else
    pathHistory.push_back({pose.gameMs,pathCm});
  nearGrid.center(body.position);
  farGrid.center(body.position);
  if(rayStamps.size()<rays.size())
    rayStamps.resize(rays.size());

  uint64_t oldest = pose.gameMs;
  std::vector<const NavigationEye::Ray*> water;
  for(size_t i=0; i<rays.size(); ++i) {
    const auto& ray = rays[i];
    oldest = std::min(oldest,ray.sampledAtMs);
    auto& stamp = rayStamps[i];
    if((stamp.seen && ray.sampledAtMs<=stamp.gameMs) || ray.sampledAtMs>pose.gameMs ||
       !finite(ray.origin) || !finite(ray.point) || !std::isfinite(ray.distanceCm) || ray.distanceCm<=0.f)
      continue;
    stamp.gameMs = ray.sampledAtMs;
    stamp.seen = true;
    const float observedPath = pathAt(ray.sampledAtMs);
    ingest(nearGrid,ray,observedPath);
    ingest(farGrid,ray,observedPath);
    if(ray.hit && ray.kind==NavigationEye::HitKind::Water &&
       std::none_of(water.begin(),water.end(),[&](const auto* other) {
         return other->sampledAtMs==ray.sampledAtMs && (other->point-ray.point).quadLength()<1.e-4f;
         }))
      water.push_back(&ray);
    }
  reconstructWater(nearGrid,water);
  reconstructWater(farGrid,water);
  // Empty ticks must retain history until the next eye output arrives. With an
  // output, keep the predecessor of its oldest cached timestamp for interpolation.
  if(!rays.empty()) {
    const auto end = std::lower_bound(pathHistory.begin(),pathHistory.end(),oldest,
                                     [](const PathStamp& stamp, uint64_t time) { return stamp.gameMs<time; });
    if(end!=pathHistory.begin())
      pathHistory.erase(pathHistory.begin(),end-1);
    }
  }

void NavigationMemory::ingest(Grid& grid, const NavigationEye::Ray& ray, float observedPath) {
  const Vec3 delta = ray.point-ray.origin;
  const float segmentLengthSquared = delta.x*delta.x+delta.y*delta.y+delta.z*delta.z;
  const float inverseLengthSquared = segmentLengthSquared>1.e-12f ? 1.f/segmentLengthSquared : 0.f;
  const auto remember = [&](WorldCell& cell, Channel channel, float height) {
    auto& evidence = cell.evidence[channel];
    if(ray.sampledAtMs<evidence.gameMs)
      return;
    if((channel==Clearance || channel==Wall) && (cell.known&(1u<<channel))!=0 && height>evidence.heightCm)
      return; // A high ray does not refresh or replace an older low observation.
    if(ray.sampledAtMs==evidence.gameMs && (cell.known&(1u<<channel))!=0 &&
       (channel==Clearance || channel==Wall))
      height = std::min(height,evidence.heightCm);
    evidence = {ray.sampledAtMs,observedPath,height,ray.point.x,ray.point.z};
    cell.known |= uint8_t(1u<<channel);
    };

  const int hitX = cellCoordinate(ray.point.x,grid.cellCm);
  const int hitZ = cellCoordinate(ray.point.z,grid.cellCm);

  // Clip the projected segment, then traverse only crossed cells (2D DDA).
  // There is deliberately no interpolation across the gaps between eye rays.
  float enter = 0, exit = 1;
  const auto clip = [&](float origin, float direction, float low, float high) {
    if(std::abs(direction)<1.e-7f)
      return origin>=low && origin<high;
    float a = (low-origin)/direction, b = (high-origin)/direction;
    if(a>b)
      std::swap(a,b);
    enter = std::max(enter,a);
    exit = std::min(exit,b);
    return enter<=exit;
    };
  const float minX = float(grid.minX)*grid.cellCm, minZ = float(grid.minZ)*grid.cellCm;
  if(clip(ray.origin.x,delta.x,minX,minX+float(grid.size)*grid.cellCm) &&
     clip(ray.origin.z,delta.z,minZ,minZ+float(grid.size)*grid.cellCm)) {
    const float inset = 1.e-6f;
    const float start = std::min(exit,enter+inset);
    int x = cellCoordinate(ray.origin.x+delta.x*start,grid.cellCm);
    int z = cellCoordinate(ray.origin.z+delta.z*start,grid.cellCm);
    x = std::clamp(x,grid.minX,grid.minX+int(grid.size)-1);
    z = std::clamp(z,grid.minZ,grid.minZ+int(grid.size)-1);
    const int stepX = delta.x>0.f ? 1 : delta.x<0.f ? -1 : 0;
    const int stepZ = delta.z>0.f ? 1 : delta.z<0.f ? -1 : 0;
    const float infinity = std::numeric_limits<float>::infinity();
    const float dtx = stepX==0 ? infinity : grid.cellCm/std::abs(delta.x);
    const float dtz = stepZ==0 ? infinity : grid.cellCm/std::abs(delta.z);
    float tx = stepX==0 ? infinity : (float(x+(stepX>0 ? 1 : 0))*grid.cellCm-ray.origin.x)/delta.x;
    float tz = stepZ==0 ? infinity : (float(z+(stepZ>0 ? 1 : 0))*grid.cellCm-ray.origin.z)/delta.z;
    float t = enter;
    while(grid.contains(x,z) && t<=exit) {
      const float end = std::min({tx,tz,exit});
      if(!(ray.hit && x==hitX && z==hitZ)) {
        auto& cell = grid.at(x,z);
        const float a = ray.origin.y+delta.y*t, b = ray.origin.y+delta.y*end;
        remember(cell,Free,std::min(a,b));
        remember(cell,Clearance,std::min(a,b));
        // Sharing a projected cell (especially a two-metre far cell) does not
        // mean this ray crossed the old hit. Only a newer free segment within
        // clearHeightCm of its actual 3D position contradicts that observation.
        // Water is a static horizontal surface. Empty air above it, including
        // a nearby ray within the clearance tolerance, does not contradict it.
        for(Channel channel:{Ground,Wall,Movable,Surface}) {
          const auto& evidence = cell.evidence[channel];
          if((cell.known&(1u<<channel))!=0 && ray.sampledAtMs>evidence.gameMs &&
             evidence.heightCm>=std::min(a,b)-Constants::clearHeightCm &&
             evidence.heightCm<=std::max(a,b)+Constants::clearHeightCm) {
            const float dx = evidence.hitX-ray.origin.x, dy = evidence.heightCm-ray.origin.y;
            const float dz = evidence.hitZ-ray.origin.z;
            const float along = std::clamp((dx*delta.x+dy*delta.y+dz*delta.z)*inverseLengthSquared,
                                           t,std::max(t,end));
            const float ex = dx-delta.x*along, ey = dy-delta.y*along, ez = dz-delta.z*along;
            if(ex*ex+ey*ey+ez*ez<=Constants::clearHeightCm*Constants::clearHeightCm)
              cell.known &= uint8_t(~(1u<<channel));
            }
          }
        }
      if(end>=exit)
        break;
      const bool advanceX = tx<=tz, advanceZ = tz<=tx;
      t = end;
      if(advanceX) { x += stepX; tx += dtx; }
      if(advanceZ) { z += stepZ; tz += dtz; }
      }
    }
  if(!ray.hit || !grid.contains(hitX,hitZ))
    return;
  auto& cell = grid.at(hitX,hitZ);
  using Kind = NavigationEye::HitKind;
  if(ray.kind==Kind::MovableVob || ray.kind==Kind::Npc)
    remember(cell,Movable,ray.point.y);
  else if(ray.kind==Kind::Water)
    remember(cell,Water,ray.point.y);
  else if(ray.normalY>=Constants::groundNormalY)
    remember(cell,Ground,ray.point.y);
  else if(std::abs(ray.normalY)<=Constants::wallNormalY)
    remember(cell,Wall,ray.point.y);
  else
    remember(cell,Surface,ray.point.y);
  }

void NavigationMemory::reconstructWater(Grid& grid, const std::vector<const NavigationEye::Ray*>& observations) {
  // Reconstruct only small patches bracketed by this eye sample's water hits.
  // Isolated points and gaps wider than a metre remain unchanged; there is no
  // extrapolation to an unseen lake boundary or connection through dry ground.
  constexpr float maxEdgeCm = 100.f, surfaceToleranceCm = 10.f, dryGroundCm = 2.f;
  constexpr size_t maxNeighbours = 6;
  struct Neighbour final { size_t index = 0; float distanceSquared = 0; };
  for(size_t i=0; i<observations.size(); ++i) {
    const auto& a = *observations[i];
    std::vector<Neighbour> neighbours;
    for(size_t j=0; j<observations.size(); ++j) {
      if(i==j)
        continue;
      const auto& b = *observations[j];
      const float dx = b.point.x-a.point.x, dz = b.point.z-a.point.z;
      const float distanceSquared = dx*dx+dz*dz;
      if(a.sampledAtMs==b.sampledAtMs && std::abs(a.point.y-b.point.y)<=surfaceToleranceCm &&
         distanceSquared<=maxEdgeCm*maxEdgeCm)
        neighbours.push_back({j,distanceSquared});
      }
    std::sort(neighbours.begin(),neighbours.end(),[](const Neighbour& a, const Neighbour& b) {
      return a.distanceSquared<b.distanceSquared;
      });
    if(neighbours.size()>maxNeighbours)
      neighbours.resize(maxNeighbours);
    for(size_t j=0; j<neighbours.size(); ++j)
      for(size_t k=j+1; k<neighbours.size(); ++k) {
        if(neighbours[j].index<i || neighbours[k].index<i)
          continue; // Each patch is owned by its first observed vertex.
        const auto& b = *observations[neighbours[j].index];
        const auto& c = *observations[neighbours[k].index];
        const float bx = b.point.x-a.point.x, bz = b.point.z-a.point.z;
        const float cx = c.point.x-a.point.x, cz = c.point.z-a.point.z;
        const float edgeX = c.point.x-b.point.x, edgeZ = c.point.z-b.point.z;
        if(edgeX*edgeX+edgeZ*edgeZ>maxEdgeCm*maxEdgeCm ||
           std::abs(b.point.y-c.point.y)>surfaceToleranceCm)
          continue;
        const float area = bx*cz-bz*cx;
        if(std::abs(area)<1.f)
          continue;
        const int x0 = std::max(grid.minX,cellCoordinate(std::min({a.point.x,b.point.x,c.point.x}),grid.cellCm));
        const int x1 = std::min(grid.minX+int(grid.size)-1,cellCoordinate(std::max({a.point.x,b.point.x,c.point.x}),grid.cellCm));
        const int z0 = std::max(grid.minZ,cellCoordinate(std::min({a.point.z,b.point.z,c.point.z}),grid.cellCm));
        const int z1 = std::min(grid.minZ+int(grid.size)-1,cellCoordinate(std::max({a.point.z,b.point.z,c.point.z}),grid.cellCm));
        for(int z=z0; z<=z1; ++z)
          for(int x=x0; x<=x1; ++x) {
            const float px = (float(x)+.5f)*grid.cellCm, pz = (float(z)+.5f)*grid.cellCm;
            const float dx = px-a.point.x, dz = pz-a.point.z;
            const float u = (dx*cz-dz*cx)/area, v = (bx*dz-bz*dx)/area;
            if(u<0.f || v<0.f || u+v>1.f)
              continue;
            const float height = a.point.y+u*(b.point.y-a.point.y)+v*(c.point.y-a.point.y);
            auto& cell = grid.at(x,z);
            if((cell.known&(1u<<Ground))!=0 && cell.evidence[Ground].heightCm>height+dryGroundCm)
              continue;
            auto& evidence = cell.evidence[Water];
            if((cell.known&(1u<<Water))!=0 && evidence.gameMs>a.sampledAtMs)
              continue;
            evidence = {a.sampledAtMs,pathAt(a.sampledAtMs),height,px,pz};
            cell.known |= uint8_t(1u<<Water);
            }
        }
    }
  }

float NavigationMemory::confidence(const Evidence& evidence, bool movable) const {
  const double age = double(body.gameMs-evidence.gameMs)*.001;
  const double distance = std::max(0.,pathCm-double(evidence.pathCm));
  const double tau = movable ? Constants::movableTauSeconds : Constants::staticTauSeconds;
  return float(std::exp(-age/tau-distance/double(Constants::distanceLambdaCm)));
  }

void NavigationMemory::read(const Grid& grid, Layer& result) const {
  result.size = grid.size;
  result.cellCm = grid.cellCm;
  result.cells.resize(grid.size*grid.size);
  std::fill(result.cells.begin(),result.cells.end(),Cell{});
  const Vec3 right(-body.forward.z,0,body.forward.x);
  const float step = std::isfinite(body.stepHeightCm) && body.stepHeightCm>0.f ?
                     body.stepHeightCm : Constants::stepCm;
  const float slopeDegrees = std::isfinite(body.maxSlopeDegrees) ?
                             std::clamp(body.maxSlopeDegrees,0.f,89.f) : Constants::maxSlopeDegrees;
  const float slope = std::tan(slopeDegrees*3.14159265358979323846f/180.f);
  const float bodyHeight = std::isfinite(body.bodyHeightCm) && body.bodyHeightCm>0.f ?
                           body.bodyHeightCm : Constants::bodyHeightCm;
  for(int z=grid.minZ; z<grid.minZ+int(grid.size); ++z)
    for(int x=grid.minX; x<grid.minX+int(grid.size); ++x) {
      const auto& input = grid.at(x,z);
      if(input.known==0)
        continue;
      const float dx = (float(x)+.5f)*grid.cellCm-body.position.x;
      const float dz = (float(z)+.5f)*grid.cellCm-body.position.z;
      const int vx = cellCoordinate(dx*right.x+dz*right.z+float(grid.size/2)*grid.cellCm,grid.cellCm);
      const int vz = cellCoordinate(dx*body.forward.x+dz*body.forward.z+float(grid.size/2)*grid.cellCm,grid.cellCm);
      if(vx<0 || vz<0 || vx>=int(grid.size) || vz>=int(grid.size))
        continue;
      auto& output = result.cells[size_t(vz)*grid.size+size_t(vx)];
      std::array<float,ChannelCount> c = {};
      for(size_t channel=0; channel<ChannelCount; ++channel)
        if((input.known&(1u<<channel))!=0) {
          c[channel] = confidence(input.evidence[channel],channel==Movable);
          output.confidence = std::max(output.confidence,c[channel]);
          }
      output.free = std::max(output.free,c[Free]);
      // A hit above an observed walking surface does not prove a wall in the
      // space occupied by the body. Nearby ground is evidence, not a guess at
      // the height of unseen terrain. Without it the wall remains conservative.
      bool overheadWall = false;
      if(c[Wall]>0.f) {
        const auto& wall = input.evidence[Wall];
        float nearest = std::numeric_limits<float>::infinity();
        for(int nz=z-1; nz<=z+1; ++nz)
          for(int nx=x-1; nx<=x+1; ++nx) {
            if(!grid.contains(nx,nz))
              continue;
            const auto& neighbour = grid.at(nx,nz);
            if((neighbour.known&(1u<<Ground))==0)
              continue;
            const auto& ground = neighbour.evidence[Ground];
            const float gx = ground.hitX-wall.hitX, gz = ground.hitZ-wall.hitZ;
            const float distance = gx*gx+gz*gz;
            if(distance<nearest) {
              nearest = distance;
              overheadWall = wall.heightCm>ground.heightCm+bodyHeight;
              }
            }
        }
      if(!overheadWall)
        output.wall = std::max(output.wall,c[Wall]);
      output.water = std::max(output.water,c[Water]);
      output.movable = std::max(output.movable,c[Movable]);
      if(c[Ground]>0.f) {
        const auto& ground = input.evidence[Ground];
        output.groundHeightCm = ground.heightCm;
        output.free = std::max(output.free,c[Ground]);
        // Height relative to the actor's feet says nothing about traversability
        // several metres ahead. A hazard needs a locally observed discontinuity
        // exceeding both the body's step and its maximum walking slope.
        for(int nz=z-1; nz<=z+1; ++nz)
          for(int nx=x-1; nx<=x+1; ++nx) {
            if((nx==x && nz==z) || !grid.contains(nx,nz))
              continue;
            const auto& neighbour = grid.at(nx,nz);
            if((neighbour.known&(1u<<Ground))==0)
              continue;
            const auto& other = neighbour.evidence[Ground];
            const float difference = ground.heightCm-other.heightCm;
            const float dx = ground.hitX-other.hitX, dz = ground.hitZ-other.hitZ;
            const float distance = std::sqrt(dx*dx+dz*dz);
            if(std::abs(difference)<=step || std::abs(difference)<=distance*slope)
              continue;
            const float certainty = std::min(c[Ground],confidence(other,false));
            if(difference>0.f)
              output.ledge = std::max(output.ledge,certainty);
            else
              output.drop = std::max(output.drop,certainty);
            }
        }
      // Empty air below a ray is not a ground measurement: it cannot establish
      // a drop without a local observed change in the supporting surface.
      }
  }

void NavigationMemory::view(View& result) const {
  result.pose = body;
  result.traveledCm = pathCm;
  read(nearGrid,result.near);
  read(farGrid,result.far);
  }
